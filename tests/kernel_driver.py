"""Drive the whole notebook in a real marimo kernel, the way the browser does.

`drive(module, scenario)` runs every cell of the module's notebook, then `await scenario(book)`,
where the book reads the kernel's globals and sets or presses a control. What the kernel
reported comes back as the sink, so a test can ask which cells raised. A stop cancelling the
cells below it is not an exception. app.run() cannot do this: it runs the cells once, in order,
and never re-runs one for a changed control.
"""

import asyncio
import json
import queue


class Sink:
    """The kernel's stream: keeps what it is told, so a test can look for errors."""

    def __init__(self):
        self.messages = []

    def write(self, data):
        self.messages.append(data)

    def exceptions(self):
        """(cell, message) for every cell that raised. A stop cancelling a descendant is no error."""
        found = []
        for raw in self.messages:
            note = json.loads(raw)
            data = note.get("data", note)
            output = data.get("output") or {}
            if output.get("channel") == "marimo-error":
                found += [
                    (data["cell_id"], e["msg"]) for e in output["data"] if e["type"] == "exception"
                ]
        return found


class Book:
    """A kernel that has run every cell, and the ways a browser touches it."""

    def __init__(self, kernel):
        self.kernel = kernel

    def g(self, name):
        return self.kernel.globals[name]

    async def set(self, name, frontend_value):
        from marimo._runtime.commands import UpdateUIElementCommand

        element = self.g(name)  # fetched afresh: a re-run cell makes a new element
        command = UpdateUIElementCommand.from_ids_and_values([(element._id, frontend_value)])
        await self.kernel.set_ui_element_value(command, notify_frontend=False)

    async def press(self, name):
        await self.set(name, 1)  # a run button counts its clicks


def drive(module, scenario):
    """Run `await scenario(book)` in a kernel that has executed every cell of module's notebook."""
    from marimo._ast.app import InternalApp
    from marimo._ast.app_config import _AppConfig
    from marimo._config.config import DEFAULT_CONFIG
    from marimo._messaging.types import KernelStreams, Stream
    from marimo._runtime.commands import AppMetadata, ExecuteCellCommand
    from marimo._runtime.kernel_lifecycle import KernelArgs, kernel_session
    from marimo._session.model import SessionMode

    class KernelSink(Sink, Stream):
        pass

    app = InternalApp(module.app)
    sink = KernelSink()

    async def go():
        meta = AppMetadata(query_params={}, cli_args={}, app_config=_AppConfig(), argv=None)
        args = KernelArgs(
            streams=KernelStreams(stream=sink, stdout=None, stderr=None, stdin=None),
            debugger=None, configs={}, app_metadata=meta, user_config=DEFAULT_CONFIG,
            mode=SessionMode.EDIT, control_queue=queue.Queue(), set_ui_element_queue=queue.Queue(),
            virtual_file_storage=None,
        )  # fmt: skip
        with kernel_session(args) as (kernel, _):
            everything = [
                ExecuteCellCommand(cell_id=cid, code=data._cell.code)
                for cid, data in app.cell_manager.valid_cells()
            ]
            await kernel.run(everything)
            await scenario(Book(kernel))

    asyncio.run(go())
    return sink
