"""IM channel adapters package — import providers for side-effect registration."""

from app.services.channels.base import create_adapter, list_providers, get_adapter_class  # noqa: F401
from app.services.channels import mock as _mock  # noqa: F401
from app.services.channels import feishu as _feishu  # noqa: F401
from app.services.channels import dingtalk as _dingtalk  # noqa: F401
from app.services.channels import telegram as _telegram  # noqa: F401
from app.services.channels import qq as _qq  # noqa: F401
from app.services.channels import wecom as _wecom  # noqa: F401
