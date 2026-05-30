"""mbcli — read Mercedes me vehicle status by reusing a saved browser session."""

__version__ = "0.1.0"

# Where headless status capture navigates (the vehicle dashboard).
DASHBOARD_URL = (
    "https://www.me.mercedes-benz.com/passengercars/my-area/my-dashboard.html"
)

# Where interactive login starts — the main site, which routes to sign-in.
# Starting here avoids the dashboard deep-link's b2x LOGIN token callback.
LOGIN_URL = "https://www.me.mercedes-benz.com/"
