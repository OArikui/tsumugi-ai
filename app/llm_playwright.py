import logging
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, Playwright, sync_playwright

from app.llm_base import LLMBackend

logger = logging.getLogger(__name__)


class ChatGPTPlaywright(LLMBackend):
    INPUT_SELECTORS = [
        "#prompt-textarea",
        "div[contenteditable='true'][data-placeholder]",
        "textarea[data-testid='prompt-textarea']",
    ]

    SEND_SELECTORS = [
        "button[data-testid='send-button']",
        "button[aria-label='送信']",
        "button[aria-label*='Send']",
    ]

    STOP_SELECTORS = [
        "button[aria-label='回答を停止']",
        "button[aria-label='回答を停止する']",
        "button[data-testid='stop-button']",
    ]

    def __init__(
        self,
        chatgpt_url: str,
        browser_profile_dir: str,
        system_prompt: str,
        chat_timeout: float = 3600.0,
        stable_seconds: float = 3.0,
        poll_interval: float = 0.5,
    ) -> None:
        self.chatgpt_url = chatgpt_url
        self.browser_profile_dir = browser_profile_dir
        self.system_prompt = system_prompt
        self.max_wait_seconds = chat_timeout
        self.stable_seconds = stable_seconds
        self.poll_interval = poll_interval
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._system_prompt_sent = False

    def _start(self) -> None:
        """Edgeを起動し、ChatGPTを開く。"""
        if self.page is not None:
            return
        profile_dir = Path(self.browser_profile_dir).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="msedge",
                headless=False,
                args=[
                    "--start-maximized",
                    # Cloudflare の人間確認がループする対策:
                    # 自動操作ブラウザであることを示すフラグを抑える
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            # navigator.webdriver を消してボット判定を緩和する
            self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
        except Exception as exc:
            raise RuntimeError("Microsoft Edgeの起動に失敗しました。") from exc
        self.page = self.context.new_page()
        try:
            self.page.goto(self.chatgpt_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            raise RuntimeError("ChatGPTのページを開くことができませんでした。") from exc

    def _wait_for_login(self) -> Locator:
        """ログインしていない場合、手動ログインを待つ。"""
        if self.page is None:
            raise RuntimeError("ChatGPTのページがありません。")
        input_box = self._find_input()
        if input_box is not None:
            return input_box
        print()
        print("=" * 55)
        print("ChatGPTへのログインが必要です。")
        print("Edgeの画面でChatGPTに手動ログインしてください。")
        print("ログイン完了後、この画面でEnterを押してください。")
        print("=" * 55)
        input("ログイン完了後、Enterを押してください: ")
        try:
            self.page.bring_to_front()
        except Exception:
            logger.warning("Edgeの画面を前面に表示できませんでした。")
        deadline = time.monotonic() + self.max_wait_seconds
        while time.monotonic() < deadline:
            input_box = self._find_input()
            if input_box is not None:
                logger.info("ChatGPTへのログインを確認しました。")
                return input_box
            time.sleep(self.poll_interval)
        raise RuntimeError(
            "ログイン後も入力欄が見つかりません。"
            "ChatGPTの画面を確認してください。"
        )

    def _find_input(self) -> Locator | None:
        """入力欄を複数セレクタで探す。"""
        if self.page is None:
            return None
        for selector in self.INPUT_SELECTORS:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=500):
                    return locator
            except Exception:
                continue
        return None

    def _find_send_button(self) -> Locator | None:
        """送信ボタンを複数セレクタで探す。"""
        if self.page is None:
            return None
        for selector in self.SEND_SELECTORS:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=500):
                    return locator
            except Exception:
                continue
        return None

    def _find_stop_button(self) -> bool:
        """生成停止ボタンが表示されているか。"""
        if self.page is None:
            return False
        for selector in self.STOP_SELECTORS:
            try:
                if self.page.locator(selector).first.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    def _find_assistant_messages(self) -> Locator:
        """アシスタントの発言要素を返す。0件でも例外にしない（新規チャット対応）。"""
        if self.page is None:
            raise RuntimeError("ChatGPTのページがありません。")
        return self.page.locator("[data-message-author-role='assistant']")

    def _get_latest_response(self) -> str:
        """最新のアシスタントの発言を取得する。"""
        messages = self._find_assistant_messages()
        count = messages.count()
        if count == 0:
            return ""
        return messages.nth(count - 1).inner_text().strip()

    def _send_message(self, text: str) -> None:
        """入力欄に文章を入力して送信する。"""
        input_box = self._find_input()
        if input_box is None:
            raise RuntimeError("ChatGPTの入力欄が見つかりません。")
        input_box.click()
        input_box.fill(text)
        send_button = self._find_send_button()
        if send_button is not None:
            send_button.click()
        else:
            input_box.press("Enter")

    def _wait_for_response(self, previous_count: int) -> str:
        """返答生成が完了するまでテキストが安定するまで待つ。"""
        deadline = time.monotonic() + self.max_wait_seconds
        last_text = ""
        last_change = time.monotonic()
        response_started = False
        while time.monotonic() < deadline:
            try:
                assistant_messages = self._find_assistant_messages()
                count = assistant_messages.count()
                current_text = (
                    assistant_messages.nth(count - 1).inner_text().strip()
                    if count > previous_count
                    else ""
                )
                if count > previous_count and current_text:
                    response_started = True
                if current_text != last_text:
                    last_text = current_text
                    last_change = time.monotonic()
                stop_visible = self._find_stop_button()
                if (
                    response_started
                    and current_text
                    and not stop_visible
                    and time.monotonic() - last_change >= self.stable_seconds
                ):
                    return current_text
            except Exception as exc:
                logger.debug("返答取得中: %s", exc)
            time.sleep(self.poll_interval)
        if response_started and last_text:
            logger.warning("返答の安定待ちがタイムアウトしました。")
            return last_text
        raise RuntimeError(
            "ChatGPTの返答を取得できませんでした。"
            "通信状態やChatGPTの画面構成を確認してください。"
        )

    def start(self) -> None:
        """Edgeを起動し、ログイン待ちまで行う。"""
        self._start()
        self._wait_for_login()

    def chat(self, user_text: str) -> str:
        """ユーザーの文章を送信し、返答を取得する。"""
        if not user_text.strip():
            raise ValueError("送信する文章が空です。")
        self._start()
        if self.page is None:
            raise RuntimeError("ChatGPTのページが初期化されていません。")
        self._wait_for_login()
        # 初回だけ会話の最初にシステムプロンプトを送信する。
        if not self._system_prompt_sent and self.system_prompt.strip():
            logger.info("システムプロンプトを送信しています。")
            previous_count = self._find_assistant_messages().count()
            self._send_message(self.system_prompt)
            self._wait_for_response(previous_count)
            self._system_prompt_sent = True
        if self._find_input() is None:
            raise RuntimeError("ChatGPTの入力欄が見つかりません。")
        previous_count = self._find_assistant_messages().count()
        logger.info("ChatGPTにメッセージを送信しています。")
        self._send_message(user_text)
        response = self._wait_for_response(previous_count)
        logger.info("ChatGPTの返答を取得しました。")
        return response

    def close(self) -> None:
        """EdgeとPlaywrightを終了する。"""
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.page = None
