import random
import time
from string import ascii_letters

import pytest

from mmpy_bot import Message, Plugin, listen_to

from .utils import MAIN_BOT_ID, OFF_TOPIC_ID, RESPONSE_TIMEOUT, TEAM_ID
from .utils import driver as driver_fixture
from .utils import parameterize_bot

# Hacky workaround to import the fixture without linting errors
driver = driver_fixture


class DirectPlugin(Plugin):
    @listen_to("^direct reply (.*)")
    async def reply_direct(self, message: Message, text):
        self.driver.reply_to(message, f"Telling you privately! {text}", direct=True)


@pytest.fixture(scope="module")
def start_bot(request):
    return parameterize_bot(request, plugins=[DirectPlugin()])


# Verifies that the bot is running and listening to this non-targeted message
def test_start_direct(start_bot, driver):
    def bot_and_user_direct_channel(channel):
        """Find which channels are direct and have the user and bot as participants."""
        name = channel["name"]

        user_chan = driver.user_id in name
        bot_chan = MAIN_BOT_ID in name
        # D = direct message channel
        direct = channel["type"] == "D"

        return user_chan and bot_chan and direct

    # Create a random string of text so we can uniquely identify the bot reply
    random_string = "".join(random.choices(ascii_letters, k=30))
    trigger = f"direct reply {random_string}"
    reply = f"Telling you privately! {random_string}"

    # The bot should reply with a direct message
    # which is implemented by mattermost as a channel
    driver.create_post(OFF_TOPIC_ID, trigger)

    user_channels = driver.channels.get_channels_for_user(driver.user_id, TEAM_ID)
    channels = list(filter(bot_and_user_direct_channel, user_channels))

    # We need to wait for the reply to be processed by mattermost
    # and the private channel created
    retries = 2

    for _ in range(retries):
        if len(channels) != 1:
            time.sleep(RESPONSE_TIMEOUT)
            user_channels = driver.channels.get_channels_for_user(
                driver.user_id, TEAM_ID
            )
            channels = list(filter(bot_and_user_direct_channel, user_channels))
        else:
            channel = channels.pop()
            break
    else:
        raise ValueError("Couldn't find a direct channel between user and bot")

    posts = driver.posts.get_posts_for_channel(channel["id"])

    for _ in range(retries):
        for post in posts["posts"].values():
            if post["message"] == reply:
                return

        time.sleep(RESPONSE_TIMEOUT)
    else:
        raise ValueError(f"Direct reply '{reply}' not found among direct messages")
