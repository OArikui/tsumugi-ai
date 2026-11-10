from abc import ABC, abstractmethod
from typing import Callable


class LLMBackend(ABC):
    """LLMの抽象基底クラス。"""

    supports_streaming = False

    @abstractmethod
    def start(self) -> None:
        """LLMの起動と接続確認を行う。"""
        raise NotImplementedError

    @abstractmethod
    def chat(self, user_text: str) -> str:
        """
        ユーザーの文章を送信し、LLMの返答を取得する。

        Args:
            user_text: ユーザー入力
        Returns:
            LLMの返答
        """
        raise NotImplementedError

    def chat_streaming(
        self,
        user_text: str,
        on_text_chunk: Callable[[str], None],
        on_stream_reset: Callable[[], None] | None = None,
    ) -> str:
        """ストリーミング非対応バックエンドは従来のchatへフォールバックする。"""
        return self.chat(user_text)

    def close(self) -> None:
        """バックエンドのリソースを解放する。"""
        return None
