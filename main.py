import logging
import os
import sys
from pathlib import Path

import yaml

from app.llm_base import LLMBackend
from app.llm_gemini import GeminiPlaywright
from app.llm_playwright import ChatGPTPlaywright
from app.tts_voicevox import VoiceVoxTTS

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("config.yamlが見つかりません。")
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as yaml_file:
            config = yaml.safe_load(yaml_file)
    except yaml.YAMLError as exc:
        raise ValueError("config.yamlの読み込みに失敗しました。") from exc
    if not isinstance(config, dict):
        raise ValueError("config.yamlの形式が正しくありません。")
    required = ["voicevox_url", "speaker", "browser_profile_dir", "chatgpt_url", "system_prompt"]
    for key in required:
        if key not in config:
            raise ValueError(f"config.yamlに{key}がありません。")
    return config


def _expand_profile_dir(path_value: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(path_value)))


def _log_rms_callback(rms: float) -> None:
    logger.info("音声出力 RMS: %.4f", rms)


def create_llm(config: dict) -> LLMBackend:
    backend = str(config.get("llm_backend", "chatgpt")).lower()
    common = {
        "browser_profile_dir": _expand_profile_dir(config["browser_profile_dir"]),
        "system_prompt": config["system_prompt"],
        "cdp_port": int(config.get("cdp_port", 9222)),
        "use_real_edge_profile": bool(config.get("use_real_edge_profile", False)),
    }
    if backend == "gemini":
        return GeminiPlaywright(
            gemini_url=config.get("gemini_url", "https://gemini.google.com/app"),
            **common,
        )
    if backend == "chatgpt":
        return ChatGPTPlaywright(chatgpt_url=config["chatgpt_url"], **common)
    raise ValueError(f"未対応の llm_backend です: {backend}")


def create_tts(config: dict) -> VoiceVoxTTS:
    return VoiceVoxTTS(
        voicevox_url=config["voicevox_url"],
        speaker=int(config["speaker"]),
        rms_callback=_log_rms_callback,
    )


def main() -> int:
    llm: LLMBackend | None = None
    tts: VoiceVoxTTS | None = None
    try:
        setup_logging()
        print()
        print("春日部つむぎAIを開始します。")
        print()
        config = load_config()
        logger.info("VOICEVOXを確認しています。")
        tts = create_tts(config)
        tts.check_connection()
        logger.info("LLMを初期化しています。")
        llm = create_llm(config)
        logger.info("ChatGPTを起動しています。")
        llm.start()
        print("準備ができました。")
        print("文章を入力してください。")
        print("終了する場合は exit と入力してください。")
        while True:
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
                response = llm.chat(user_text)
                if not response.strip():
                    logger.warning("LLMから空の返答が返されました。")
                    continue
                print()
                print(f"春日部つむぎ: {response}")
                print()
                tts.speak(response)
            except KeyboardInterrupt:
                logger.info("処理を中断しました。")
                break
            except Exception as exc:
                logger.error("対話処理に失敗しました: %s", exc, exc_info=True)
                print("エラーが発生しました。")
                print(f"原因: {exc}")
    except KeyboardInterrupt:
        logger.info("終了します。")
        return 0
    except Exception as exc:
        logger.error("アプリケーションの初期化に失敗しました: %s", exc, exc_info=True)
        print(f"エラー: {exc}")
        return 1
    finally:
        logger.info("リソースを解放しています。")
        if llm is not None:
            llm.close()
        if tts is not None:
            tts.close()
        print("春日部つむぎAIを終了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
