from typing import Optional

from sqlmodel import Field
from fastapi_amis_admin.amis.components import PageSchema

from gsuid_core.webconsole import site
from gsuid_core.webconsole.mount_app import GsAdminModel
from gsuid_core.utils.database.base_models import Bind, Type, T_Bind

from ..utils import convert_list


class SsBind(Bind, table=True):
    __table_args__ = {"extend_existing": True}
    uid: str = Field(default=None, title="自选股票")
    push: Optional[str] = Field(
        title="股票状态推送",
        default="off",
        schema_extra={"json_schema_extra": {"hint": "开启股票推送"}},
    )

    @classmethod
    async def delete_uid(
        cls: Type[T_Bind],
        user_id: str,
        bot_id: str,
        uid: str,
        game_name: Optional[str] = None,
    ) -> int:
        result = await cls.get_uid_list_by_game(user_id, bot_id, game_name)
        if result is None:
            return -1

        result = convert_list(result)

        if uid not in result:
            return -1

        result.remove(uid)

        result = [i for i in result if i] if result else []
        new_uid = "_".join(result)

        if not new_uid:
            new_uid = None

        await cls.update_data(
            user_id,
            bot_id,
            **{cls.get_gameid_name(game_name): new_uid},
        )
        return 0


@site.register_admin
class SsPushAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="股票自选管理",
        icon="fa fa-bullhorn",
    )  # type: ignore

    # 配置管理模型
    model = SsBind
