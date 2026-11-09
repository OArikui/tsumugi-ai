import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, Locator, Page, Playwright, sync_playwright

from app.llm_base import LLMBackend

logger = logging.getLogger(__name__)


class ChatGPTPlaywright(LLMBackend):
    # サービスのタブを見分けるためのホスト名（ログイン画面のドメインも含む）
    PAGE_HOSTS = ["chatgpt.com", "auth.openai.com"]
    LOGIN_SERVICE = "ChatGPT"

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
        cdp_port: int = 9222,
        use_real_edge_profile: bool = False,
        login_callback: Callable[[str], bool] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.chatgpt_url = chatgpt_url
        self.browser_profile_dir = browser_profile_dir
        self.system_prompt = system_prompt
        self.max_wait_seconds = chat_timeout
        self.stable_seconds = stable_seconds
        self.poll_interval = poll_interval
        self.cdp_port = cdp_port
        self.use_real_edge_profile = use_real_edge_profile
        self.login_callback = login_callback
        self.stop_event = stop_event
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.edge_process: subprocess.Popen | None = None
        self._system_prompt_sent = False

    @staticmethod
    def _find_edge_executable() -> str:
        """msedge.exe のパスを探す。"""
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        found = shutil.which("msedge")
        if found:
            return found
        raise RuntimeError(
            "Microsoft Edge (msedge.exe) が見つかりません。"
            "Edgeがインストールされているか確認してください。"
        )

    def _resolve_profile_dir(self) -> Path:
        """使用するEdgeプロファイルのディレクトリを返す。

        実プロファイルモードでは普段使いの Edge の User Data を使う。
        作りたての専用プロファイルは Cloudflare に信用されにくいため。
        """
        if self.use_real_edge_profile:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise RuntimeError("LOCALAPPDATA が取得できませんでした。")
            profile_dir = Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
            if not profile_dir.is_dir():
                raise RuntimeError(
                    "普段使いのEdgeプロファイルが見つかりません: "
                    f"{profile_dir}"
                )
            return profile_dir
        profile_dir = Path(self.browser_profile_dir).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir

    def _start(self) -> None:
        """Edgeを通常アプリとして起動し、CDPで接続してChatGPTを開く。

        Playwrightから直接起動すると自動操作フラグが付き、Cloudflareの
        人間確認がループするため、Edgeは通常プロセスとして先に起動し、
        Playwrightは connect_over_cdp で後から接続する。
        """
        if self.page is not None:
            return
        profile_dir = self._resolve_profile_dir()
        self.playwright = sync_playwright().start()
        endpoint = f"http://127.0.0.1:{self.cdp_port}"
        # 既に同じポートでEdgeが起動していなければ、通常プロセスとして起動する
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(endpoint, timeout=30_000)
        except Exception:
            self.browser = None
        if self.browser is None:
            edge_path = self._find_edge_executable()
            logger.info("Microsoft Edgeを起動しています（CDPポート: %s）", self.cdp_port)
            launch_args = [
                edge_path,
                f"--remote-debugging-port={self.cdp_port}",
                f"--user-data-dir={profile_dir}",
                "--start-maximized",
            ]
            if self.use_real_edge_profile:
                launch_args.append("--profile-directory=Default")
            launch_args.append(self.chatgpt_url)
            self.edge_process = subprocess.Popen(launch_args)
            deadline = time.monotonic() + 30.0
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    self.browser = self.playwright.chromium.connect_over_cdp(
                        endpoint, timeout=30_000
                    )
                    break
                except Exception as exc:  # Edgeの起動待ちでリトライする
                    last_error = exc
                    time.sleep(1.0)
            if self.browser is None:
                message = "Microsoft EdgeへのCDP接続に失敗しました。"
                if self.use_real_edge_profile:
                    message += (
                        "Edgeが既に起動している場合は、すべてのウィンドウを閉じて"
                        "（タスクマネージャーで msedge.exe が残っていない状態にして）"
                        "から再実行してください。"
                    )
                raise RuntimeError(message) from last_error
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = self.browser.new_context()
        # navigator.webdriver を消してボット判定をさらに緩和する
        try:
            self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
        except Exception:
            pass
        pages = self.context.pages
        matched = next(
            (p for p in pages if any(host in p.url for host in self.PAGE_HOSTS)),
            None,
        )
        if matched is not None:
            # サービスのタブ（ログイン途中のタブも含む）があればそれを使う
            self.page = matched
            return
        if pages and not self.use_real_edge_profile:
            self.page = pages[0]
        else:
            self.page = self.context.new_page()
        if not self.page.url.startswith(self.chatgpt_url):
            try:
                self.page.goto(self.chatgpt_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                raise RuntimeError("ChatGPTのページを開くことができませんでした。") from exc

    def _check_stop_requested(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("アプリケーションの終了要求を受け取りました。")

    def _wait_for_login(self) -> Locator:
        """ログインしていない場合、GUIコールバックまたはCLI入力で完了を待つ。"""
        if self.page is None:
            raise RuntimeError("ChatGPTのページがありません。")
        self._check_stop_requested()
        input_box = self._find_input()
        if input_box is not None:
            return input_box

        if self.login_callback is not None:
            logger.info("%sへのログインをGUIで待機します。", self.LOGIN_SERVICE)
            if not self.login_callback(self.LOGIN_SERVICE):
                raise RuntimeError("ログイン待ちがキャンセルされました。")
        else:
            print()
            print("=" * 55)
            print(f"{self.LOGIN_SERVICE}へのログインが必要です。")
            print(f"Edgeの画面で{self.LOGIN_SERVICE}に手動ログインしてください。")
            print("ログイン完了後、この画面でEnterを押してください。")
            print("=" * 55)
            input("ログイン完了後、Enterを押してください: ")

        self._check_stop_requested()
        try:
            self.page.bring_to_front()
        except Exception:
            logger.warning("Edgeの画面を前面に表示できませんでした。")
        deadline = time.monotonic() + self.max_wait_seconds
        while time.monotonic() < deadline:
            self._check_stop_requested()
            input_box = self._find_input()
            if input_box is not None:
                logger.info("%sへのログインを確認しました。", self.LOGIN_SERVICE)
                return input_box
            time.sleep(self.poll_interval)
        raise RuntimeError(
            f"ログイン後も入力欄が見つかりません。{self.LOGIN_SERVICE}の画面を確認してください。"
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
            self._check_stop_requested()
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
        self._check_stop_requested()
        self._start()
        self._wait_for_login()

    def chat(self, user_text: str) -> str:
        """ユーザーの文章を送信し、返答を取得する。"""
        if not user_text.strip():
            raise ValueError("送信する文章が空です。")
        self._check_stop_requested()
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
        """Playwrightの接続を切り、自分で起動したEdgeを終了する。"""
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        self.context = None
        # 実プロファイルモードでは普段使いのEdgeを巻き込まないよう終了しない
        if self.edge_process is not None and not self.use_real_edge_profile:
            try:
                if self.edge_process.poll() is None:
                    self.edge_process.terminate()
                    try:
                        self.edge_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.edge_process.kill()
            except Exception:
                pass
            self.edge_process = None
        elif self.use_real_edge_profile:
            self.edge_process = None
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.page = None
