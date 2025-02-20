from typing import Optional

from sqlmodel import Field
from gsuid_core.webconsole import site
from gsuid_core.utils.database.base_models import Bind
from gsuid_core.webconsole.mount_app import GsAdminModel
from fastapi_amis_admin.amis.components import PageSchema


class SsBind(Bind, table=True):
    __table_args__ = {'extend_existing': True}
    uid: str = Field(default=None, title='自选股票')
    push: Optional[str] = Field(
        title='股票状态推送',
        default='off',
        schema_extra={'json_schema_extra': {'hint': '开启股票推送'}},
    )


@site.register_admin
class SsPushAdmin(GsAdminModel):
    pk_name = 'id'
    page_schema = PageSchema(
        label='股票自选管理',
        icon='fa fa-bullhorn',
    )  # type: ignore

    # 配置管理模型
    model = SsBind
