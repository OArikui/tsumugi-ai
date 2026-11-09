"""春日部つむぎAIの起動・対話処理。"""

import logging
import os
import sys

from pathlib import Path

import yaml

from app.console_ui import ConsoleUI
from app.llm_base import LLMBackend
from app.llm_gemini import GeminiPlaywright
from app.llm_playwright import ChatGPTPlaywright
from app.live2d_viewer import Live2DViewer
from app.tts_voicevox import VoiceVoxTTS


APP_VERSION = "0.6.0"

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def setup_logging() -> None:
    """ログ出力を設定する。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


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


def create_llm(config: dict) -> LLMBackend:
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
    if not bool(config.get("live2d_enabled", False)):
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
    ui: ConsoleUI,
    viewer: Live2DViewer | None,
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
        playback_callback=ui.update_playback,
    )


def main() -> int:
    """アプリケーションのメイン処理。"""
    setup_logging()

    logger.info("春日部つむぎAI v%s を開始します。", APP_VERSION)

    llm: LLMBackend | None = None
    tts: VoiceVoxTTS | None = None
    viewer: Live2DViewer | None = None
    ui: ConsoleUI | None = None

    print()
    print("=" * 50)
    print(f"       春日部つむぎAI v{APP_VERSION}")
    print("=" * 50)
    print()

    try:
        config = load_config()
        ui = ConsoleUI()

        # Live2Dは起動に失敗しても音声対話を継続する。
        viewer = create_live2d_viewer(config)

        logger.info("VOICEVOXを確認しています。")
        tts = create_tts(config, ui, viewer)
        tts.check_connection()

        logger.info("LLMを初期化しています。")
        llm = create_llm(config)

        logger.info("AIを起動しています。")
        with ui.processing("AIを起動しています"):
            llm.start()

        print()
        print("準備ができました。")
        print("文章を入力すると、春日部つむぎが返答します。")
        print("終了する場合は exit と入力してください。")
        print(
            "[Live2D] アバター表示: "
            + ("有効" if viewer is not None else "無効")
        )
        print()

        while True:
            print("[待機中] 文章を入力してください。")
            try:
                user_text = input("あなた: ").strip()
            except EOFError:
                break

            if user_text.lower() == "exit":
                break
            if not user_text:
                continue

            try:
                logger.info("LLMに問い合わせています。")
                with ui.processing("AIの返答を待っています"):
                    response = llm.chat(user_text)

                if not response.strip():
                    logger.warning("LLMから空の返答が返されました。")
                    continue

                print()
                print(f"春日部つむぎ: {response}")
                print()

                try:
                    with ui.processing("音声を合成して再生しています"):
                        tts.speak(response)
                finally:
                    if viewer is not None:
                        viewer.set_mouth_open(0.0)

            except KeyboardInterrupt:
                logger.info("処理を中断しました。")
                if viewer is not None:
                    viewer.set_mouth_open(0.0)
                break
            except Exception as exc:
                if viewer is not None:
                    viewer.set_mouth_open(0.0)

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
        logger.info("リソースを解放しています。")

        if viewer is not None:
            try:
                viewer.set_mouth_open(0.0)
                viewer.close()
            except Exception:
                logger.exception("Live2Dの終了処理に失敗しました。")

        if llm is not None:
            try:
                llm.close()
            except Exception:
                logger.exception("LLMの終了処理に失敗しました。")

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
    sys.exit(main())
