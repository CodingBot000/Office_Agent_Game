from app.config import Settings
from app.providers.base import AgentProvider, IntentProvider, SocialImpactProvider
from app.providers.cli import CliDecisionProvider, CliIntentProvider, CliSocialImpactProvider
from app.providers.deterministic import (
    DeterministicDecisionProvider,
    DeterministicIntentProvider,
    DeterministicSocialImpactProvider,
)
from app.providers.openai import OpenAIDecisionProvider, OpenAIIntentProvider, OpenAISocialImpactProvider
from app.providers.base import ReportProvider
from app.providers.cli import CliReportProvider
from app.providers.openai import OpenAIReportProvider
from app.providers.deterministic_report import DeterministicReportProvider


def create_report_provider(settings: Settings) -> ReportProvider:
    if settings.ai_provider == "cli":
        return CliReportProvider(settings)
    if settings.ai_provider == "openai":
        return OpenAIReportProvider(settings)
    return DeterministicReportProvider()


def create_provider(settings: Settings) -> AgentProvider:
    if settings.ai_provider == "cli":
        return CliDecisionProvider(settings)
    if settings.ai_provider == "openai":
        return OpenAIDecisionProvider(settings)
    return DeterministicDecisionProvider()


def create_intent_provider(settings: Settings) -> IntentProvider:
    if settings.ai_provider == "cli":
        return CliIntentProvider(settings)
    if settings.ai_provider == "openai":
        return OpenAIIntentProvider(settings)
    return DeterministicIntentProvider()


def create_social_impact_provider(settings: Settings) -> SocialImpactProvider:
    if settings.ai_provider == "cli":
        return CliSocialImpactProvider(settings)
    if settings.ai_provider == "openai":
        return OpenAISocialImpactProvider(settings)
    return DeterministicSocialImpactProvider()
