from app.config import Settings
from app.models import IntentClassification
from app.providers.base import AgentProvider, DecisionContext, IntentContext, IntentProvider, ProviderError
from app.providers.cli import CliDecisionProvider, CliIntentProvider
from app.providers.deterministic import DeterministicDecisionProvider, DeterministicIntentProvider


class UnimplementedOpenAIDecisionProvider:
    """Configuration placeholder until the remote Responses adapter is added."""

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model

    def decide(self, context: DecisionContext):
        raise ProviderError("OpenAI API provider adapter is not implemented yet")


class UnimplementedOpenAIIntentProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.openai_model

    def classify(self, context: IntentContext) -> IntentClassification:
        raise ProviderError("OpenAI API intent provider is not implemented yet")


def create_provider(settings: Settings) -> AgentProvider:
    if settings.ai_provider == "cli":
        return CliDecisionProvider(settings)
    if settings.ai_provider == "openai":
        return UnimplementedOpenAIDecisionProvider(settings)
    return DeterministicDecisionProvider()


def create_intent_provider(settings: Settings) -> IntentProvider:
    if settings.ai_provider == "cli":
        return CliIntentProvider(settings)
    if settings.ai_provider == "openai":
        return UnimplementedOpenAIIntentProvider(settings)
    return DeterministicIntentProvider()
