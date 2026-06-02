from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

bcrypt = Bcrypt()
csrf = CSRFProtect()
limiter = Limiter(get_remote_address, default_limits=["200 per day", "50 per hour"])
