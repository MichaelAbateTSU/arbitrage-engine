from app.config import get_settings

settings = get_settings()
print(
    f"Valid: environment={settings.environment} source={settings.data_mode} trading=paper"
)
print("Live execution: unavailable")
