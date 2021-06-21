import re
from unittest import mock

import click

from mmpy_bot import Message, Plugin, Settings, listen_to, listen_webhook
from mmpy_bot.driver import Driver
from mmpy_bot.plugins import PluginManager


# Used in the plugin tests below
class FakePlugin(Plugin):
    """Hello FakePlugin.

    This is a plugin level docstring
    """

    @listen_to("pattern", needs_mention=True)
    def my_function(self, message, another_arg=True):
        """This is the docstring of my_function."""
        pass

    @listen_to("direct_pattern", direct_only=True, allowed_users=["admin"])
    def direct_function(self, message):
        """Help direct function."""
        pass

    @listen_to("async_pattern")
    @listen_to("another_async_pattern", direct_only=True)
    async def my_async_function(self, message):
        """Async function docstring."""
        pass

    @listen_to("click_command")
    @click.command(help="Help string for the entire function.")
    @click.option(
        "--option", type=int, default=0, help="Help string for the optional argument."
    )
    def click_commmand(self, message, option):
        """Extended docstring.

        This docstring will have 'click --help' appended
        """
        pass

    @listen_to("hi_custom", custom="Custom attribute")
    def hi_custom(self, message):
        """A custom function."""
        pass

    @listen_webhook("webhook_id")
    def webhook_listener(self, event):
        """A webhook function."""
        pass


def expand_func_names(f):
    return [f] + f.siblings


msg_listeners = {
    re.compile("pattern"): expand_func_names(FakePlugin.my_function),
    re.compile("direct_pattern"): expand_func_names(FakePlugin.direct_function),
    re.compile("another_async_pattern"): expand_func_names(
        FakePlugin.my_async_function
    ),
    re.compile("async_pattern"): expand_func_names(FakePlugin.my_async_function),
    re.compile("hi_custom"): expand_func_names(FakePlugin.hi_custom),
    # Click commands construct a regex pattern from the listen_to pattern
    re.compile("^click_command (.*)?"): expand_func_names(FakePlugin.click_commmand),
}

hook_listeners = {
    re.compile("webhook_id"): expand_func_names(FakePlugin.webhook_listener)
}


class TestPlugin:
    def test_init_plugin(self):
        p = FakePlugin()
        m = PluginManager([p])
        with mock.patch.object(p, "initialize") as mocked:
            m.initialize_manager(Driver(), Settings())

            mocked.assert_called_once()

    def test_initialize(self):
        m = PluginManager([FakePlugin()])
        m.initialize_manager(Driver(), Settings())

        # Test whether the function was registered properly
        assert m.message_listeners[re.compile("pattern")] == [
            FakePlugin.my_function,
        ]

        # This function should be registered twice, once for each listener
        assert len(m.message_listeners[re.compile("async_pattern")]) == 1
        assert (
            m.message_listeners[re.compile("async_pattern")][0].function
            == FakePlugin.my_async_function.function
        )

        assert len(m.message_listeners[re.compile("another_async_pattern")]) == 1
        assert (
            m.message_listeners[re.compile("another_async_pattern")][0].function
            == FakePlugin.my_async_function.function
        )

        assert len(m.webhook_listeners) == 1
        assert m.webhook_listeners[re.compile("webhook_id")] == [
            FakePlugin.webhook_listener
        ]


def create_message(
    text="hello",
    mentions=["qmw86q7qsjriura9jos75i4why"],
    channel_type="O",
    sender_name="betty",
):
    return Message(
        {
            "event": "posted",
            "data": {
                "channel_display_name": "Off-Topic",
                "channel_name": "off-topic",
                "channel_type": channel_type,
                "mentions": mentions,
                "post": {
                    "id": "wqpuawcw3iym3pq63s5xi1776r",
                    "create_at": 1533085458236,
                    "update_at": 1533085458236,
                    "edit_at": 0,
                    "delete_at": 0,
                    "is_pinned": "False",
                    "user_id": "131gkd5thbdxiq141b3514bgjh",
                    "channel_id": "4fgt3n51f7ftpff91gk1iy1zow",
                    "root_id": "",
                    "parent_id": "",
                    "original_id": "",
                    "message": text,
                    "type": "",
                    "props": {},
                    "hashtags": "",
                    "pending_post_id": "",
                },
                "sender_name": sender_name,
                "team_id": "au64gza3iint3r31e7ewbrrasw",
            },
            "broadcast": {
                "omit_users": "None",
                "user_id": "",
                "channel_id": "4fgt3n51f7ftpff91gk1iy1zow",
                "team_id": "",
            },
            "seq": 29,
        }
    )


class TestPluginManager:
    def setup_method(self):
        self.p = FakePlugin()
        self.manager = PluginManager([self.p])

    def test_init(self):
        self.manager.initialize_manager(Driver(), Settings())

        # Test that listeners of individual plugins are now registered on the manager
        assert len(msg_listeners) == len(self.manager.message_listeners)
        for pattern, listeners in self.manager.message_listeners.items():
            for listener in listeners:
                assert pattern in msg_listeners
                assert listener in msg_listeners[pattern]

        assert len(hook_listeners) == len(self.manager.webhook_listeners)
        for pattern, listeners in self.manager.webhook_listeners.items():
            for listener in listeners:
                assert pattern in hook_listeners
                assert listener in hook_listeners[pattern]

    def test_iteration(self):
        assert list(self.manager) == self.manager.plugins

    def test_get_help(self):
        # Prior to initialization there is no help
        assert self.manager.get_help() == []

        self.manager.initialize_manager(Driver(), Settings())

        assert len(self.manager.get_help()) != 0

        for hlp in self.manager.get_help():
            assert hlp.location == "FakePlugin"
            assert hlp.plugin_docheader == "Hello FakePlugin."
            assert (
                hlp.plugin_docfull
                == """Hello FakePlugin.

    This is a plugin level docstring
    """
            )
            assert hlp.is_click == hlp.function.is_click_function
            assert hlp.function_docfull.startswith(hlp.function.__doc__)

            if hlp.pattern in ["direct_pattern", "another_async_pattern"]:
                assert hlp.direct
            else:
                assert not hlp.direct

            if hlp.pattern in ["pattern"]:
                assert hlp.mention
            else:
                assert not hlp.mention

            if hlp.help_type == "message":
                assert hlp.pattern in map(lambda x: x.pattern, msg_listeners)
            elif hlp.help_type == "webhook":
                assert hlp.pattern in map(lambda x: x.pattern, hook_listeners)