from app.config import Settings
from app.providers.base import AgentProvider, DecisionContext, ProviderError
from app.providers.cli import CliDecisionProvider
from app.providers.deterministic import DeterministicDecisionProvider


class UnimplementedOpenAIDecisionProvider:
    """Configuration placeholder until the remote Responses adapter is added."""

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model

    def decide(self, context: DecisionContext):
        raise ProviderError("OpenAI API provider adapter is not implemented yet")


def create_provider(settings: Settings) -> AgentProvider:
    if settings.ai_provider == "cli":
        return CliDecisionProvider(settings)
    if settings.ai_provider == "openai":
        return UnimplementedOpenAIDecisionProvider(settings)
    return DeterministicDecisionProvider()
