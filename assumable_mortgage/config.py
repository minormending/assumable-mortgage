import os
from dataclasses import dataclass


@dataclass
class AssumableConfig:
    token: str
    xsrf_token: str | None
    cf_clearance: str | None
    botble_session: str | None
    remember_account_name: str | None
    remember_account: str | None

    @staticmethod
    def from_env() -> "AssumableConfig":
        return AssumableConfig(
            token=os.getenv("ASSUMABLE_TOKEN", ""),
            xsrf_token=os.getenv("XSRF_TOKEN"),
            cf_clearance=os.getenv("CF_CLEARANCE"),
            botble_session=os.getenv("BOTBLE_SESSION"),
            remember_account_name=os.getenv("REMEMBER_ACCOUNT_NAME"),
            remember_account=os.getenv("REMEMBER_ACCOUNT"),
        )


@dataclass
class GreatSchoolsConfig:
    user_agent: str
    csrf_token: str | None
    csrf_cookie: str | None
    city: str

    @staticmethod
    def from_env() -> "GreatSchoolsConfig":
        return GreatSchoolsConfig(
            user_agent=os.getenv(
                "GS_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            ),
            csrf_token=os.getenv("GS_CSRF_TOKEN"),
            csrf_cookie=os.getenv("GS_COOKIE"),
            city=os.getenv("GS_CITY", "The Bronx"),
        )

