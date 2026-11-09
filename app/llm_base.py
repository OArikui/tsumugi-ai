from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """LLMの抽象基底クラス。"""

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

    def close(self) -> None:
        """バックエンドのリソースを解放する。"""
        return None
