"""
Shared pytest configuration for the tests/ directory.
Registers CLI options used by integration tests.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--username",
        default=None,
        help="Config username to load Binance credentials for (e.g. alice)",
    )
    parser.addoption(
        "--api-key",
        default="pvNNdpY4k2NjQf6TqiNaH9Y5V9pU3csw75OpM0zZyOoUkeoounQlVEdibiimUcuk",
        help="Binance API key (overrides config file / .env)",
    )
    parser.addoption(
        "--api-secret",
        default="kn056IiUmvPbMeF3U8MmlZ4PVqKcY1hMXxLLs2ZBOhxu3vSQIDBszUNpYkzKE6C7",
        help="Binance API secret (overrides config file / .env)",
    )
    parser.addoption(
        "--testnet",
        action="store_true",
        default=False,
        help="Use Binance Testnet instead of live",
    )
