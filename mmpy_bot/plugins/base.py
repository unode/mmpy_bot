from __future__ import annotations

import logging
import re
from abc import ABC
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, ItemsView, List, Optional, Sequence

from mmpy_bot.driver import Driver
from mmpy_bot.function import Function, MessageFunction, WebHookFunction, listen_to
from mmpy_bot.settings import Settings
from mmpy_bot.wrappers import EventWrapper, Message

log = logging.getLogger("mmpy.plugin_base")


def caller(driver):
    """Implements a callback with access to the mattermost driver."""

    async def call_function(
        function: Function,
        event: EventWrapper,
        groups: Optional[Sequence[str]] = [],
    ):
        if function.is_coroutine:
            await function(event, *groups)  # type:ignore
        else:
            # By default, we use the global threadpool of the driver, but we could use
            # a plugin-specific thread or process pool if we wanted.
            driver.threadpool.add_task(function, event, *groups)

    return call_function


class Plugin(ABC):
    """A Plugin is a self-contained class that defines what functions should be executed
    given different inputs.

    It will be called by the EventHandler whenever one of its listeners is triggered,
    but execution of the corresponding function is handled by the plugin itself. This
    way, you can implement multithreading or multiprocessing as desired.
    """

    def __init__(
        self, direct_help: bool = False,
    ):
        self.driver: Optional[Driver] = None
        self.settings: Optional[Settings] = None
        self.direct_help: bool = direct_help
        self.call_function: Optional[Callable] = None

    def initialize(self, driver: Driver, settings: Optional[Settings] = None):
        self.driver = driver
        self.settings = settings
        self.call_function = caller(driver)

    def on_start(self):
        """Will be called after initialization.

        Can be overridden on the subclass if desired.
        """
        log.debug(f"Plugin {self.__class__.__name__} started!")
        return self

    def on_stop(self):
        """Will be called when the bot is shut down manually.

        Can be overridden on the subclass if desired.
        """
        log.debug(f"Plugin {self.__class__.__name__} stopped!")
        return self

    def get_help_string(self):
        string = f"Plugin {self.__class__.__name__} has the following functions:\n"
        string += "----\n"
        for functions in self.message_listeners.values():
            for function in functions:
                string += f"- {function.get_help_string()}"
            string += "----\n"
        if len(self.webhook_listeners) > 0:
            string += "### Registered webhooks:\n"
            for functions in self.webhook_listeners.values():
                for function in functions:
                    string += f"- {function.get_help_string()}"

        return string

    async def help(self, message: Message):
        """Prints the list of functions registered on every active plugin."""
        self.driver.reply_to(message, self.get_help_string(), direct=self.direct_help)


@dataclass
class PluginHelp:
    help_type: str
    location: str
    function: str
    pattern: str
    doc_header: str
    doc_full: str
    direct: bool
    mention: bool
    annotations: Dict


class PluginManager:
    """PluginManager is responsible for initializing all plugins and display aggregated
    help from each of them.

    It is supposed to be transparent to EventHandler that interacts directly with each
    individual Plugin.
    """

    def __init__(
        self, plugins: Sequence[Plugin], direct_help: bool = True,
    ):
        self.driver: Optional[Driver] = None
        self.settings: Optional[Settings] = None
        self.plugins: Sequence[Plugin] = plugins
        self.direct_help: bool = direct_help
        self.call_function: Optional[Callable] = None

        self.message_listeners: Dict[re.Pattern, List[MessageFunction]] = defaultdict(
            list
        )
        self.webhook_listeners: Dict[re.Pattern, List[WebHookFunction]] = defaultdict(
            list
        )

    def __iter__(self):
        return iter(self.plugins)

    def initialize(self, driver: Driver, settings: Settings):
        self.driver = driver
        self.settings = settings
        self.call_function = caller(driver)

        if self.settings.RESPOND_MENTION_HELP:
            self.help = listen_to("^help$", needs_mention=True)(Plugin.help)
        if self.settings.RESPOND_CHANNEL_HELP:
            help_target = (
                self.help if self.settings.RESPOND_MENTION_HELP else Plugin.help
            )
            self.help = listen_to("^!help$")(help_target)

        # Add Plugin manager help to message listeners
        self.help.plugin = self
        self.message_listeners[self.help.matcher].append(self.help)

        for plugin in self.plugins:
            plugin.initialize(self.driver, settings)

            # Register listeners for any listener functions in the plugin
            for attribute in dir(plugin):
                attribute = getattr(plugin, attribute)
                if isinstance(attribute, Function):
                    # Register this function and any potential siblings
                    for function in [attribute] + attribute.siblings:
                        # Plugin message/webhook handlers can be decorated multiple times
                        # resulting in multiple siblings that do not have plugin defined
                        # or where the relationship with the parent plugin is incorrect
                        function.plugin = plugin
                        if isinstance(function, MessageFunction):
                            self.message_listeners[function.matcher].append(function)
                        elif isinstance(function, WebHookFunction):
                            self.webhook_listeners[function.matcher].append(function)
                        else:
                            raise TypeError(
                                f"{self.__class__.__name__} has a function of unsupported"
                                f" type {type(function)}."
                            )

    def _generate_plugin_help(
        self,
        plug_help: List[PluginHelp],
        help_type: str,
        items: ItemsView[re.Pattern, List[Function]],
    ):
        for matcher, functions in items:
            for function in functions:
                doc_full = function.function.__doc__
                if doc_full is None:
                    doc_header = ""
                    doc_full = ""
                else:
                    doc_header = function.function.__doc__.split("\n", 1)[0]

                if help_type == "message":
                    direct = function.direct_only
                    mention = function.needs_mention
                elif help_type == "webhook":
                    direct = mention = False
                else:
                    raise NotImplementedError(f"Unknown help type: '{help_type}'")

                plug_help.append(
                    PluginHelp(
                        help_type=help_type,
                        location=function.plugin.__class__.__name__,
                        function=function,
                        pattern=matcher.pattern,
                        doc_header=doc_header,
                        doc_full=doc_full,
                        direct=direct,
                        mention=mention,
                        annotations=function.annotations,
                    )
                )

    def get_help(self):
        response: List[PluginHelp] = []

        self._generate_plugin_help(response, "message", self.message_listeners.items())
        self._generate_plugin_help(response, "webhook", self.webhook_listeners.items())

        return response

    def get_help_string(self):
        def custom_sort(rec):
            return (rec.help_type, rec.pattern.lstrip("^[(-"))

        string = "### The following functions have been registered:\n\n"
        string += "###### `(*)` require the use of `@botname`, "
        string += "`(+)` can only be used in direct message\n"
        for h in sorted(self.get_help(), key=custom_sort):
            cmd = h.annotations.get("syntax", h.pattern)
            direct = "`(*)`" if h.direct else ""
            mention = "`(+)`" if h.mention else ""

            if h.help_type == "webhook":
                string += f"- `{cmd}` {direct} {mention} - (webhook) {h.doc_header}\n"
            else:
                if not h.doc_header:
                    string += f"- `{cmd}` {direct} {mention}\n"
                else:
                    string += f"- `{cmd}` {direct} {mention} - {h.doc_header}\n"

        return string
