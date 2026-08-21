import os


# Unit/API tests must not invoke a user's locally authenticated CLI provider.
# Provider-specific behavior is covered with isolated adapter tests instead.
os.environ["AI_PROVIDER"] = "deterministic-mock"
