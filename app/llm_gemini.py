import logging
import time

from playwright.sync_api import Locator

from app.llm_playwright import ChatGPTPlaywright

logger = logging.getLogger(__name__)


class GeminiPlaywright(ChatGPTPlaywright):
    """Gemini Web (gemini.google.com) を操作するバックエンド。

    Edge の起動と CDP 接続、送信と返答待ちの仕組みは
    ChatGPTPlaywright をそのまま利用し、Gemini 用のセレクタと
    ログイン案内だけを差し替える。
    """

    PAGE_HOSTS = ["gemini.google.com", "accounts.google.com"]
    LOGIN_SERVICE = "Gemini"

    INPUT_SELECTORS = [
        "div.ql-editor[contenteditable='true']",
        "rich-textarea div[contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
    ]

    SEND_SELECTORS = [
        "button[aria-label='送信']",
        "button[aria-label='Send']",
        "button.send-button",
    ]

    STOP_SELECTORS = [
        "button[aria-label='生成を停止']",
        "button[aria-label='回答を停止']",
        "button[aria-label='Stop generating']",
    ]

    def __init__(
        self,
        gemini_url: str,
        browser_profile_dir: str,
        system_prompt: str,
        **kwargs,
    ) -> None:
        super().__init__(
            chatgpt_url=gemini_url,
            browser_profile_dir=browser_profile_dir,
            system_prompt=system_prompt,
            **kwargs,
        )
        self.gemini_url = gemini_url

    def _find_assistant_messages(self) -> Locator:
        """Gemini の回答要素を返す。0件でも例外にしない。"""
        if self.page is None:
            raise RuntimeError("Geminiのページがありません。")
        return self.page.locator("model-response message-content")

    def _wait_for_login(self) -> Locator:
        """Geminiのログイン待ちは共通のGUI/CLI対応処理へ委譲する。"""
        return super()._wait_for_login()
