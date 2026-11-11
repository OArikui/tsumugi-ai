"""春日部つむぎAIの起動・対話処理。"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading

from datetime import datetime
from pathlib import Path


APP_VERSION = "0.12.2"

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_FILE_HANDLER: logging.FileHandler | None = None
_LOG_FILE_PATH: Path | None = None


def _create_log_file_handler(log_dir: Path) -> tuple[logging.FileHandler, Path]:
    """既存の命名規則でログファイルハンドラを作成する。"""
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"tsumugi-{timestamp}.log"
    suffix = 1
    while log_path.exists():
        log_path = log_dir / f"tsumugi-{timestamp}-{suffix:02d}.log"
        suffix += 1

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler, log_path


def _initialize_bootstrap_logging() -> None:
    """外部・アプリモジュールのimport前に標準ライブラリだけでログを準備する。"""
    global _LOG_FILE_HANDLER, _LOG_FILE_PATH

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handlers: list[logging.Handler] = [console_handler]
    log_error: OSError | None = None

    try:
        _LOG_FILE_HANDLER, _LOG_FILE_PATH = _create_log_file_handler(
            PROJECT_ROOT / "logs"
        )
        handlers.append(_LOG_FILE_HANDLER)
    except OSError as exc:
        log_error = exc

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=handlers,
        force=True,
    )

    if _LOG_FILE_PATH is not None:
        logger.info("ログファイル: %s", _LOG_FILE_PATH)
    elif log_error is not None:
        logger.warning(
            "ログファイルを作成できません。コンソール出力のみ継続します。原因: %s",
            log_error,
        )


_initialize_bootstrap_logging()

# import時の例外も、アプリ本体の読み込み前に準備したログへtracebackを記録する。
try:
    import yaml

    from app.console_ui import ConsoleUI
    from app.llm_base import LLMBackend
    from app.llm_gemini import GeminiPlaywright
    from app.llm_playwright import ChatGPTPlaywright
    from app.live2d_viewer import Live2DViewer
    from app.stt_whisper import SpeechToText
    from app.tts_voicevox import VoiceVoxTTS
except Exception:
    logger.exception("起動時のモジュール読み込みに失敗しました。")
    logging.shutdown()
    raise


def setup_logging(config: dict | None = None) -> None:
    """起動前に準備したログを維持し、設定に応じてファイル出力先を調整する。"""
    global _LOG_FILE_HANDLER, _LOG_FILE_PATH

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 初期化時にすでにコンソール・ファイルハンドラを登録済み。
    if config is None:
        return

    if not bool(config.get("log_to_file", True)):
        if _LOG_FILE_HANDLER is not None:
            root_logger.removeHandler(_LOG_FILE_HANDLER)
            _LOG_FILE_HANDLER.close()
            _LOG_FILE_HANDLER = None
            _LOG_FILE_PATH = None
        return

    try:
        log_dir = Path(_resolve_project_path(config.get("log_dir", "logs"))).resolve()
        if (
            _LOG_FILE_HANDLER is not None
            and _LOG_FILE_PATH is not None
            and _LOG_FILE_PATH.parent.resolve() == log_dir
        ):
            return

        new_handler, new_path = _create_log_file_handler(log_dir)
    except OSError as exc:
        logger.warning(
            "ログファイルを作成できません。既存のログ出力を維持します。原因: %s",
            exc,
        )
        return

    old_handler = _LOG_FILE_HANDLER
    root_logger.addHandler(new_handler)
    _LOG_FILE_HANDLER = new_handler
    _LOG_FILE_PATH = new_path

    if old_handler is not None:
        root_logger.removeHandler(old_handler)
        old_handler.close()

    logger.info("ログファイル: %s", new_path)

def load_config() -> dict:
    """config.yamlを読み込む。"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("config.yamlが見つかりません。")

    try:
        with CONFIG_PATH.open("r", encoding="utf-8-sig") as yaml_file:
            config = yaml.safe_load(yaml_file)
    except yaml.YAMLError as exc:
        raise ValueError("config.yamlの読み込みに失敗しました。") from exc

    if not isinstance(config, dict):
        raise ValueError("config.yamlの形式が正しくありません。")

    required = [
        "voicevox_url",
        "speaker",
        "browser_profile_dir",
        "chatgpt_url",
        "system_prompt",
    ]
    for key in required:
        if key not in config:
            raise ValueError(f"config.yamlに{key}がありません。")

    return config


EMOTIONS = {"normal", "happy", "sad", "shocked", "shy"}
EMOTION_TAG_RE = re.compile(r"\[emotion:([^\]\r\n]*)\]", re.IGNORECASE)
LEADING_EMOTION_TAG_RE = re.compile(
    r"^\s*\[emotion:([^\]\r\n]*)\]\s*",
    re.IGNORECASE,
)


def parse_emotion_response(response: str) -> tuple[str, str]:
    """先頭の感情タグを解析し、読み上げ文字列からすべてのタグを除去する。"""
    emotion = "normal"
    match = LEADING_EMOTION_TAG_RE.match(response)
    if match:
        candidate = match.group(1).strip().casefold()
        if candidate in EMOTIONS:
            emotion = candidate
        else:
            logger.warning(
                "未対応の感情タグ %r をnormalとして扱います。",
                match.group(1),
            )
    else:
        logger.warning("LLMの返答に先頭の感情タグがありません。normalとして扱います。")

    # 先頭以外に誤って出力されたタグも読み上げない。
    spoken_text = response[match.end():] if match else response
    spoken_text = EMOTION_TAG_RE.sub("", spoken_text)
    return emotion, spoken_text.strip()


def _expand_profile_dir(path_value: str) -> str:
    """Edgeプロファイルのパスを展開する。"""
    return os.path.expandvars(os.path.expanduser(str(path_value)))


def _resolve_project_path(path_value: str) -> str:
    """相対パスをプロジェクトルート基準で絶対パスに変換する。"""
    expanded = os.path.expandvars(os.path.expanduser(str(path_value)))
    path = Path(expanded)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def create_llm(config: dict, login_callback=None, stop_event=None) -> LLMBackend:
    """設定に応じてLLMバックエンドを生成する。"""
    backend = str(config.get("llm_backend", "chatgpt")).lower()

    common = {
        "browser_profile_dir": _expand_profile_dir(
            config["browser_profile_dir"]
        ),
        "system_prompt": config["system_prompt"],
        "cdp_port": int(config.get("cdp_port", 9222)),
        "use_real_edge_profile": bool(
            config.get("use_real_edge_profile", False)
        ),
        "login_callback": login_callback,
        "stop_event": stop_event,
    }

    if backend == "gemini":
        return GeminiPlaywright(
            gemini_url=config.get(
                "gemini_url",
                "https://gemini.google.com/app",
            ),
            **common,
        )

    if backend == "chatgpt":
        return ChatGPTPlaywright(
            chatgpt_url=config["chatgpt_url"],
            **common,
        )

    raise ValueError(f"未対応のllm_backendです: {backend}")


def create_live2d_viewer(config: dict) -> Live2DViewer | None:
    """Live2Dが正常に起動した場合だけビューアを返す。"""
    if not bool(config.get("live2d_enabled", True)):
        logger.info("Live2Dは無効です。")
        return None

    viewer: Live2DViewer | None = None
    try:
        model_path = _resolve_project_path(
            config.get(
                "live2d_model_path",
                "./assets/live2d/tsumugi/model.model3.json",
            )
        )

        viewer = Live2DViewer(
            model_path=model_path,
            width=int(config.get("live2d_width", 480)),
            height=int(config.get("live2d_height", 640)),
            mouth_scale=float(config.get("live2d_mouth_scale", 3.0)),
            mouth_param=str(config.get("live2d_mouth_param", "")),
            idle_sway=bool(config.get("live2d_idle_sway", True)),
            expressions=config.get("live2d_expressions"),
            natural_idle=bool(config.get("live2d_natural_idle", True)),
            motion_enabled=bool(config.get("live2d_motion_enabled", True)),
            speaking_motion_scale=float(config.get("live2d_speaking_motion_scale", 2.0)),
        )

        if viewer.start():
            logger.info("Live2Dアバターを有効にしました。")
            return viewer

        logger.warning(
            "Live2Dを利用できないため、アバターなしで続行します。"
        )
    except Exception:
        logger.exception(
            "Live2Dの初期化に失敗しました。アバターなしで続行します。"
        )

    if viewer is not None:
        try:
            viewer.close()
        except Exception:
            logger.exception("Live2Dの終了処理に失敗しました。")

    return None


def create_tts(
    config: dict,
    ui: ConsoleUI | None,
    viewer,
    playback_callback=None,
    stop_event=None,
) -> VoiceVoxTTS:
    """VOICEVOXを生成し、再生進捗と口パクのコールバックを接続する。"""
    mouth_scale = float(config.get("live2d_mouth_scale", 3.0))

    def rms_callback(rms: float) -> None:
        """音声チャンクのRMSをLive2Dの口の開き具合へ変換する。"""
        logger.debug("音声出力 RMS: %.4f", rms)

        if viewer is None or not viewer.is_running:
            return

        mouth_open = min(
            1.0,
            max(0.0, float(rms) * mouth_scale),
        )
        viewer.set_mouth_open(mouth_open)

    return VoiceVoxTTS(
        voicevox_url=config["voicevox_url"],
        speaker=int(config["speaker"]),
        rms_callback=rms_callback,
        split_by_sentence=bool(
            config.get("tts_split_by_sentence", False)
        ),
        playback_callback=(
            playback_callback
            if playback_callback is not None
            else (ui.update_playback if ui is not None else None)
        ),
        stop_event=stop_event,
    )


def create_stt(config: dict) -> SpeechToText | None:
    """設定に応じて音声認識インスタンスを生成する。モデルのロードは呼び出し側で行う。"""
    if not bool(config.get("stt_enabled", True)):
        logger.info("音声入力は設定で無効です。")
        return None

    return SpeechToText(
        model_name=str(config.get("stt_model", "small")),
        language=str(config.get("stt_language", "ja")),
        mode=str(config.get("stt_mode", "push_to_talk")),
    )


def run_gui(config: dict) -> int:
    """PySide6 GUIモードを起動する。"""
    try:
        from app.gui import run_gui as start_gui
    except ImportError:
        logger.exception(
            "GUIを起動できません。PySide6をインストールしてください。"
            "従来のCLIモードを使う場合は gui_enabled: false にしてください。"
        )
        return 1

    def llm_factory(login_callback, stop_event):
        return create_llm(
            config,
            login_callback=login_callback,
            stop_event=stop_event,
        )

    def tts_factory(viewer, playback_callback, stop_event):
        return create_tts(
            config,
            ui=None,
            viewer=viewer,
            playback_callback=playback_callback,
            stop_event=stop_event,
        )

    def stt_factory():
        return create_stt(config)

    return start_gui(
        config=config,
        llm_factory=llm_factory,
        tts_factory=tts_factory,
        stt_factory=stt_factory,
        parse_response=parse_emotion_response,
    )


def main() -> int:
    """設定に応じてGUIまたは従来CLIを起動する。"""
    setup_logging()
    try:
        config = load_config()
        setup_logging(config)
        logger.info("春日部つむぎAI v%s を開始します。", APP_VERSION)
        if bool(config.get("gui_enabled", True)):
            return run_gui(config)
        return run_cli(config)
    except KeyboardInterrupt:
        logger.info("終了します。")
        return 0
    except Exception as exc:
        logger.error(
            "アプリケーションの初期化に失敗しました: %s",
            exc,
            exc_info=True,
        )
        return 1


def run_cli(config: dict) -> int:
    """GUIを無効にした場合の従来CLIモード。"""

    llm: LLMBackend | None = None
    tts: VoiceVoxTTS | None = None
    viewer: Live2DViewer | None = None
    stt: SpeechToText | None = None
    ui: ConsoleUI | None = None
    stop_event = threading.Event()

    print()
    print("=" *  50)
    print(f"       春日部つむぎAI v{APP_VERSION}")
    print("=" *  50)
    print()

    try:
        logger.info("春日部つむぎAI v%s を開始します。", APP_VERSION)
        ui = ConsoleUI()

        # Live2Dは起動に失敗しても音声対話を継続する。
        viewer = create_live2d_viewer(config)

        logger.info("VOICEVOXを確認しています。")
        tts = create_tts(config, ui, viewer, stop_event=stop_event)
        tts.check_connection()

        logger.info("LLMを初期化しています。")
        llm = create_llm(config, stop_event=stop_event)

        logger.info("AIを起動しています。")
        with ui.processing("AIを起動しています"):
            llm.start()

        if bool(config.get("stt_enabled", True)):
            try:
                stt = create_stt(config)
                if stt is not None:
                    logger.info(
                        "音声認識モデルを準備しています。初回はモデルのダウンロードが発生します。"
                    )
                    stt.prepare()
            except Exception:
                logger.warning(
                    "音声入力を利用できないため、テキスト入力で続行します。",
                    exc_info=True,
                )
                if stt is not None:
                    stt.close()
                stt = None

        print()
        print("準備ができました。")
        print("文章を入力すると、春日部つむぎが返答します。")
        if stt is not None and stt.is_ready:
            print("音声入力: 空Enterで録音開始、録音中にもう一度Enterで停止します。")
        else:
            print("音声入力は利用できません。テキスト入力は引き続き使用できます。")
        print("終了する場合は exit と入力してください。")
        print(
            "[Live2D] アバター表示: "
            + ("有効" if viewer is not None else "無効")
        )
        print()

        while True:
            prompt = (
                "録音中 (Enterで停止): "
                if stt is not None and stt.is_recording
                else "あなた: "
            )
            print("[待機中] 文章を入力してください。")
            try:
                user_text = input(prompt).strip()
            except EOFError:
                break

            if user_text.lower() == "exit":
                break

            if user_text and stt is not None and stt.is_recording:
                try:
                    stt.stop_recording()
                    logger.info("録音中にテキスト入力されたため、録音を停止しました。")
                except Exception:
                    logger.warning("録音停止に失敗しました。音声入力を無効化します。", exc_info=True)
                    stt.close()
                    stt = None

            if not user_text:
                if stt is None or not stt.is_ready:
                    continue
                try:
                    if not stt.is_recording:
                        stt.start_recording()
                        print("聞き取り中... もう一度Enterを押すと停止します。")
                        continue

                    user_text = stt.stop_recording_and_transcribe()
                    if not user_text:
                        print("音声を認識できませんでした。")
                        continue
                    print(f"認識結果: {user_text}")
                    if not bool(config.get("stt_auto_send", True)):
                        print("stt_auto_send=false のため送信せず、テキスト入力へ戻ります。")
                        continue
                except Exception:
                    logger.warning(
                        "音声入力に失敗しました。以降はテキスト入力で続行します。",
                        exc_info=True,
                    )
                    stt.close()
                    stt = None
                    continue

            streaming_session = None
            try:
                logger.info("LLMに問い合わせています。")
                use_streaming = (
                    bool(config.get("tts_streaming_enabled", True))
                    and bool(getattr(llm, "supports_streaming", False))
                )
                if use_streaming:
                    streaming_session = tts.start_streaming()
                    with ui.processing("AIの返答を待っています"):
                        response = llm.chat_streaming(
                            user_text,
                            streaming_session.feed_text,
                            streaming_session.abort,
                        )
                else:
                    with ui.processing("AIの返答を待っています"):
                        response = llm.chat(user_text)

                emotion, spoken_text = parse_emotion_response(response)
                if not spoken_text:
                    if streaming_session is not None:
                        streaming_session.cancel()
                    logger.warning("LLMから読み上げ可能な本文が返されませんでした。")
                    if viewer is not None:
                        viewer.set_emotion("normal")
                    continue

                if viewer is not None:
                    viewer.set_emotion(emotion)

                print()
                print(f"春日部つむぎ: {spoken_text}")
                print()

                try:
                    with ui.processing("音声を合成して再生しています"):
                        if streaming_session is not None:
                            streaming_session.finish(spoken_text)
                        else:
                            tts.speak(spoken_text)
                finally:
                    if streaming_session is not None:
                        streaming_session.cancel()
                    if viewer is not None:
                        viewer.set_mouth_open(0.0)
                        viewer.set_emotion("normal")

            except KeyboardInterrupt:
                stop_event.set()
                if streaming_session is not None:
                    streaming_session.cancel()
                logger.info("処理を中断しました。")
                if viewer is not None:
                    viewer.set_mouth_open(0.0)
                    viewer.set_emotion("normal")
                break
            except Exception as exc:
                if streaming_session is not None:
                    streaming_session.cancel()
                if viewer is not None:
                    viewer.set_mouth_open(0.0)
                    viewer.set_emotion("normal")

                logger.error(
                    "対話処理に失敗しました: %s",
                    exc,
                    exc_info=True,
                )
                print()
                print("エラーが発生しました。")
                print(f"原因: {exc}")
                print()

        return 0

    except KeyboardInterrupt:
        logger.info("終了します。")
        return 0
    except Exception as exc:
        logger.error(
            "アプリケーションの初期化に失敗しました: %s",
            exc,
            exc_info=True,
        )
        print()
        print("アプリケーションの起動に失敗しました。")
        print(f"原因: {exc}")
        return 1

    finally:
        stop_event.set()
        logger.info("リソースを解放しています。")

        if viewer is not None:
            try:
                viewer.set_mouth_open(0.0)
                viewer.set_emotion("normal")
                viewer.close()
            except Exception:
                logger.exception("Live2Dの終了処理に失敗しました。")

        if llm is not None:
            try:
                llm.close()
            except Exception:
                logger.exception("LLMの終了処理に失敗しました。")

        if stt is not None:
            try:
                stt.close()
            except Exception:
                logger.exception("音声入力の終了処理に失敗しました。")

        if tts is not None:
            try:
                tts.close()
            except Exception:
                logger.exception("VOICEVOXの終了処理に失敗しました。")

        if ui is not None:
            try:
                ui.stop()
            except Exception:
                logger.exception("ConsoleUIの終了処理に失敗しました。")

        print()
        print("春日部つむぎAIを終了しました。")


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        logger.exception("起動処理中に未捕捉の例外が発生しました。")
        exit_code = 1
    finally:
        logging.shutdown()

    sys.exit(exit_code)
