from __future__ import annotations

from setuptools import find_packages, setup

RUNTIME_DEPS = [
    "fastapi>=0.115.0",
    "httpx>=0.27.0",
    "uvicorn[standard]>=0.30.0",
]

DEV_DEPS = [
    "nox>=2025.5.1",
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "respx>=0.21.0",
    "ruff>=0.8.0",
]


setup(
    name="opencode-proxy",
    version="0.1.0",
    description="OpenAI-compatible proxy for Ollama-shaped LLM endpoints used by opencode.",
    packages=find_packages(include=["opencode_proxy", "opencode_proxy.*"]),
    python_requires=">=3.11",
    install_requires=RUNTIME_DEPS,
    extras_require={"dev": DEV_DEPS},
    entry_points={"console_scripts": ["opencode-proxy=opencode_proxy.app:main"]},
)
