"""Extension Host REST management; no CLI-specific Extension adapters."""

import click

from ..command import Group, Invocation, common, input_options, load_input, paging, segment
from ..errors import CommandError
from ..schema import partial


def route(coordinate: str) -> str:
    namespace, separator, name = coordinate.partition("/")
    if not separator or not namespace or not name or "/" in name:
        raise click.BadParameter("Extension 使用 namespace/name coordinate")
    return "/extensions/" + segment(namespace) + "/" + segment(name)


@click.group(cls=Group)
def extension():
    """管理 Peer 的 Extension 选择、配置和启停；发布由 Extension 交付流程负责。"""


@extension.command("list")
@paging
@common
@click.pass_obj
def list_extensions(inv: Invocation, limit, cursor):
    """列出共享 Extension 身份与配置；Peer 运行状态见 status。"""
    inv.show(inv.send("GET", "/extensions", params={"limit": limit, "cursor": cursor}))


@extension.command()
@click.argument("coordinate")
@common
@click.pass_obj
def get(inv: Invocation, coordinate):
    """读取共享 Extension 身份与配置；不代表任一 Peer 已启用。"""
    inv.show(inv.send("GET", route(coordinate)))


@extension.command()
@click.argument("coordinate", required=False)
@common
@click.pass_obj
def status(inv: Invocation, coordinate):
    """读取接入 Peer 的选择、运行版本、配置 schema 与重启状态。"""
    inv.show(
        inv.send(
            "POST", "/extension-management/v2", body={"action": "status", "extension": coordinate}
        )
    )


@extension.command()
@click.argument("coordinate")
@click.option("--distribution", required=True, type=click.Choice(["python", "module_federation"]))
@click.option("--version", required=True, help="准确的已发布版本，不解析 latest")
@click.option("--peer", help="选择的目标 Peer；省略时为接入 Peer")
@common
@click.pass_obj
def select(inv: Invocation, coordinate, distribution, version, peer):
    """为目标 Peer 选择准确 Distribution/版本；不隐式启用。"""
    inv.show(
        inv.send(
            "POST",
            route(coordinate) + "/selection",
            params={"route_to_peer": peer},
            body={"distribution": distribution, "version": version},
        )
    )


@extension.command()
@click.argument("coordinate")
@click.option("--peer", help="取消选择的目标 Peer；省略时为接入 Peer")
@common
@click.pass_obj
def unselect(inv: Invocation, coordinate, peer):
    """取消已禁用 Peer 的选择；保留共享 Extension 配置和信息。"""
    inv.show(inv.send("DELETE", route(coordinate) + "/selection", params={"route_to_peer": peer}))


@extension.command()
@click.argument("coordinate")
@click.option("--peer", help="启用意图的目标 Peer；省略时为接入 Peer")
@common
@click.pass_obj
def enable(inv: Invocation, coordinate, peer):
    """在指定或当前 Peer 启用 Extension。"""
    inv.show(inv.send("POST", route(coordinate) + "/enable", params={"route_to_peer": peer}))


@extension.command()
@click.argument("coordinate")
@click.option("--peer", help="禁用意图的目标 Peer；省略时为接入 Peer")
@common
@click.pass_obj
def disable(inv: Invocation, coordinate, peer):
    """在指定或当前 Peer 禁用 Extension。"""
    inv.show(inv.send("POST", route(coordinate) + "/disable", params={"route_to_peer": peer}))


@extension.group()
def config():
    """读写 Extension 的持久配置，不要求先启用。"""


@config.command("get")
@click.argument("coordinate")
@common
@click.pass_obj
def get_config(inv: Invocation, coordinate):
    """从安装记录读取 config；不调用业务 schema 验证。"""
    inv.show(inv.send("GET", route(coordinate))["config"])


def config_schema(inv: Invocation, coordinate: str) -> dict:
    result = inv.send(
        "POST", "/extension-management/v2", body={"action": "status", "extension": coordinate}
    )
    selected = next((row for row in result["extensions"] if row["name"] == coordinate), None)
    contract = selected["config_schema"] if selected else None
    if contract is None:
        raise CommandError("接入 Peer 尚未发布该 Extension 的配置 schema；请在选择并启用后重新发现")
    return contract


@config.command("replace")
@click.argument("coordinate")
@input_options
@common
@click.pass_obj
def replace_config(inv: Invocation, coordinate, schema, input_file, input_json):
    """完整替换 Extension config。"""
    if schema:
        return inv.show(config_schema(inv, coordinate))
    inv.show(
        inv.send("PUT", route(coordinate) + "/config", body=load_input(input_file, input_json))
    )


@config.command("update")
@click.argument("coordinate")
@input_options
@common
@click.pass_obj
def update_config(inv: Invocation, coordinate, schema, input_file, input_json):
    """更新提交的 config 字段。"""
    if schema:
        return inv.show(partial(config_schema(inv, coordinate)))
    inv.show(
        inv.send("PATCH", route(coordinate) + "/config", body=load_input(input_file, input_json))
    )
