import json
import os
import sys
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from discord.ui import Button, View, Modal, TextInput
from discord import ButtonStyle

import config
from log_config import get_logger
from sheet_functions import (
    create_new_spreadsheet,
    share_spreadsheet,
    fill_created_spreadsheet_with_users_except_ai_decisions,
    update_created_spreadsheet_with_users_except_ai_decisions,
    create_leaderboard_sheet,
    write_users_to_csv,
    write_ai_decisions_to_csv,
    write_users_to_csv_monthly,
    write_all_data_of_user_to_csv_by_month,
)
from leaderboard_functions import (
    create_leaderboard_by_month,
    format_leaderboard_for_discord,
    format_streaks_for_discord,
)
from db_functions import (
    insert_discord_users,
    get_ai_decisions_by_user_and_timeframe,
    calculate_monthly_streak,
)
from modals import UserModal, UserDeletionModal
from helpers import csv_to_structured_string
import utils

logger = get_logger(__name__)

intents = discord.Intents.default()
intents.messages = True
intents.members = True
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

spread_sheet_id = None
auto_post_task = None
auto_post_tasks = {}
task_details = {}

AUTH_TOKEN = config.SHARED_SECRET

# Unique identifier to manage button messages
BUTTON_MESSAGE_IDENTIFIER = "Admin Commands"


class ViewLeaderboardModal(Modal):
    def __init__(self):
        super().__init__(title="View Leaderboard")
        self.thread_id = TextInput(
            label="Thread ID", style=discord.TextStyle.short, required=True
        )
        self.date = TextInput(
            label="Date (YYYY-MM)", style=discord.TextStyle.short, required=False
        )
        self.add_item(self.thread_id)
        self.add_item(self.date)

    async def on_submit(self, interaction: discord.Interaction):
        thread_id = self.thread_id.value
        date = self.date.value

        try:
            thread = await interaction.guild.fetch_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                raise ValueError("The provided ID does not belong to a thread.")

            if date:
                year, month = date.split("-")
            else:
                now = datetime.now()
                formatted_date = now.strftime("%Y-%m")
                year, month = formatted_date.split("-")

            leaderboard = create_leaderboard_by_month(year, month)
            messages = format_leaderboard_for_discord(leaderboard)

            bot_user_id = interaction.client.user.id
            async for message in thread.history(limit=None):
                if message.author.id == bot_user_id:
                    await message.delete()

            for msg in messages:
                await thread.send(msg)

            await interaction.response.send_message(
                f"Posted to {thread_id} successfully.", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in ViewLeaderboardModal: {e}")
            await interaction.response.send_message(
                f"Please check your input: {e}", ephemeral=True
            )


class GetUserMonthlyDataModal(Modal):
    def __init__(self):
        super().__init__(title="Get User Monthly Data to CSV")
        self.username = TextInput(
            label="Username", style=discord.TextStyle.short, required=True
        )
        self.date = TextInput(
            label="Date (YYYY-MM)", style=discord.TextStyle.short, required=True
        )
        self.add_item(self.username)
        self.add_item(self.date)

    async def on_submit(self, interaction: discord.Interaction):
        username = self.username.value
        date = self.date.value

        try:
            file_path = f"user_monthly_data_{username}_{date}.csv"
            result = write_all_data_of_user_to_csv_by_month(file_path, username, date)
            if "successfully" in result:
                await interaction.channel.send(file=discord.File(file_path))
                os.remove(file_path)
                await interaction.followup.send(
                    "User monthly data is here:", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "User monthly data is not found", ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error in GetUserMonthlyDataModal: {e}")
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


class GetMonthlyStreaksModal(Modal):
    def __init__(self):
        super().__init__(title="Get Monthly Streaks")
        self.date = TextInput(
            label="Date (YYYY-MM)", style=discord.TextStyle.short, required=True
        )
        self.add_item(self.date)

    async def on_submit(self, interaction: discord.Interaction):
        date = self.date.value

        try:
            month_name = datetime.strptime(date, "%Y-%m").strftime("%B")
            streaks = calculate_monthly_streak(date)

            messages = format_streaks_for_discord(streaks, month_name)
            thread_title = f"Streaks | {date}"

            forum_channel_id = int(config.LEADERBOARD_FORUM_CHANNEL_ID)
            forum_channel = interaction.guild.get_channel(forum_channel_id)
            if not forum_channel:
                raise ValueError("Leaderboard forum channel not found.")

            thread, _ = await forum_channel.create_thread(
                name=thread_title, content=messages[0]
            )

            for msg in messages[1:]:
                await thread.send(msg)

            await interaction.followup.send(
                f"Streaks thread created: {thread.jump_url}", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in GetMonthlyStreaksModal: {e}")
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


# Define the AdminCommandsView with the desired buttons
class AdminCommandsView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(
            Button(
                label="Get User Monthly Data to CSV",
                style=ButtonStyle.primary,
                custom_id="get_user_monthly_data_to_csv_1",  # Ensure unique custom_id
            )
        )
        self.add_item(
            Button(
                label="Get Blockchain Summary",
                style=ButtonStyle.primary,
                custom_id="get_blockchain_summary_2",  # Ensure unique custom_id
            )
        )
        self.add_item(
            Button(
                label="Get All Data to CSV",
                style=ButtonStyle.secondary,
                custom_id="get_all_data_to_csv_3",  # Ensure unique custom_id
            )
        )
        self.add_item(
            Button(
                label="Get Monthly Streaks",
                style=ButtonStyle.success,
                custom_id="get_monthly_streaks_4",  # Ensure unique custom_id
            )
        )
        self.add_item(
            Button(
                label="Leaderboard View",
                style=ButtonStyle.success,
                custom_id="leaderboard_view_5",  # Ensure unique custom_id
            )
        )

    # Callback for "Get User Monthly Data to CSV" button
    async def get_user_monthly_data_to_csv_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        modal = GetUserMonthlyDataModal()
        await interaction.response.send_modal(modal)

    # Callback for "Get Blockchain Summary" button
    async def get_blockchain_summary_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await interaction.response.defer()

            url = "https://api.minaexplorer.com/summary"
            headers = {}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response_data = await response.json()

            res = json.dumps(response_data, indent=4)
            discord_message = f"```\n{res}\n```"

            await interaction.followup.send(discord_message, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in get_blockchain_summary_button: {e}")
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)

    # Callback for "Get All Data to CSV" button
    async def get_all_data_to_csv_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await interaction.response.defer()

            file_path = "all_data.csv"
            result = write_users_to_csv(file_path)
            if "successfully" in result:
                await interaction.channel.send(file=discord.File(file_path))
                os.remove(file_path)
                await interaction.followup.send("All data is here:", ephemeral=True)
            else:
                await interaction.followup.send(
                    "Failed to retrieve all data.", ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error in get_all_data_to_csv_button: {e}")
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)

    # Callback for "Get Monthly Streaks" button
    async def get_monthly_streaks_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        modal = GetMonthlyStreaksModal()
        await interaction.response.send_modal(modal)

    # Callback for "Leaderboard View" button
    async def leaderboard_view_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        modal = ViewLeaderboardModal()
        await interaction.response.send_modal(modal)


# Event: on_ready
@client.event
async def on_ready():
    try:
        await tree.sync(guild=discord.Object(id=config.GUILD_ID))
        logger.info(f"We have logged in as {client.user}")

        # Register the view for persistent interactions
        client.add_view(AdminCommandsView())

        # Fetch the test-admin channel
        test_admin_channel = client.get_channel(int(config.TEST_ADMIN_CHANNEL_ID))
        if test_admin_channel:
            # Delete existing button messages to prevent duplicates
            async for message in test_admin_channel.history(limit=100):
                if message.author == client.user:
                    # Check if the message has the identifier in its content or embed
                    if message.content.startswith(BUTTON_MESSAGE_IDENTIFIER) or (
                        message.embeds
                        and message.embeds[0].title == BUTTON_MESSAGE_IDENTIFIER
                    ):
                        await message.delete()
                        logger.info(f"Deleted old button message with ID: {message.id}")

            # Send a new button message with a unique identifier
            embed = discord.Embed(
                title=BUTTON_MESSAGE_IDENTIFIER,
                description="Use the buttons below to execute admin commands.",
                color=discord.Color.blue(),
            )
            await test_admin_channel.send(embed=embed, view=AdminCommandsView())
            logger.info("Admin commands view sent to test-admin channel.")
        else:
            logger.error(
                "Test-admin channel not found. Please check the TEST_ADMIN_CHANNEL_ID."
            )
    except Exception as e:
        logger.error(f"Error during on_ready: {e}")

    # Start the background task
    refresh_admin_buttons.start()


# Event: on_message
@client.event
async def on_message(message):
    try:
        if message.author == client.user:
            return
        # Add any additional on_message logic here if needed
    except Exception as e:
        logger.error(f"Error processing message: {e}")


# Define a background task to periodically refresh the button message if needed
@tasks.loop(hours=1)
async def refresh_admin_buttons():
    try:
        test_admin_channel = client.get_channel(int(config.TEST_ADMIN_CHANNEL_ID))
        if test_admin_channel:
            # Delete existing button messages
            async for message in test_admin_channel.history(limit=100):
                if message.author == client.user:
                    if message.content.startswith(BUTTON_MESSAGE_IDENTIFIER) or (
                        message.embeds
                        and message.embeds[0].title == BUTTON_MESSAGE_IDENTIFIER
                    ):
                        await message.delete()
                        logger.info(f"Deleted old button message with ID: {message.id}")

            # Send a new button message
            embed = discord.Embed(
                title=BUTTON_MESSAGE_IDENTIFIER,
                description="Use the buttons below to execute admin commands.",
                color=discord.Color.blue(),
            )
            await test_admin_channel.send(embed=embed, view=AdminCommandsView())
            logger.info("Refreshed admin commands view in test-admin channel.")
    except Exception as e:
        logger.error(f"Error in refresh_admin_buttons task: {e}")


# Define Slash Commands with Unique Function Names


@tree.command(
    name="commits-sheet-create",
    description="It will create a google sheet with the contributions data",
    guild=discord.Object(id=config.GUILD_ID),
)
async def commits_sheet_create_command(
    interaction: discord.Interaction, spreadsheet_name: str, email_address: str = None
):
    global spread_sheet_id
    try:
        await interaction.response.defer()
        channel = interaction.channel

        created_spreadsheet_id = create_new_spreadsheet(spreadsheet_name)

        share_spreadsheet(created_spreadsheet_id, email_address or config.GMAIL_ADDRESS)
        res = fill_created_spreadsheet_with_users_except_ai_decisions(
            created_spreadsheet_id
        )

        await interaction.followup.send(
            f"Spreadsheet is created with id: `{created_spreadsheet_id}` and name `{spreadsheet_name}`. "
            f"You can see the spreadsheet in this link: https://docs.google.com/spreadsheets/d/{created_spreadsheet_id}"
        )
        spread_sheet_id = created_spreadsheet_id
    except Exception as e:
        logger.error(f"Error in commits-sheet-create command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="commits-sheet-update",
    description="It will update the google sheet with the updated contributions data",
    guild=discord.Object(id=config.GUILD_ID),
)
async def commits_sheet_update_command(
    interaction: discord.Interaction, spreadsheet_id: str
):
    global spread_sheet_id
    try:
        await interaction.response.defer()
        channel = interaction.channel

        updated_spreadsheet_id = (
            update_created_spreadsheet_with_users_except_ai_decisions(spreadsheet_id)
        )

        await interaction.followup.send(
            f"Spreadsheet is updated with id: `{spread_sheet_id}`. "
            f"You can see the spreadsheet in this link: https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        )

        spread_sheet_id = updated_spreadsheet_id
    except Exception as e:
        logger.error(f"Error in commits-sheet-update command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="main-sheet-edit",
    description="Edit Google Sheets from Discord",
    guild=discord.Object(id=config.GUILD_ID),
)
async def main_sheet_edit_command(interaction: discord.Interaction, operation: str):
    try:
        if operation not in ["insert", "update", "add_repo", "delete"]:
            await interaction.followup.send(
                "Invalid operation. Please choose one of: insert, update, add_repo, delete.",
                ephemeral=True,
            )
            return

        modal = UserModal(operation=operation)
        await interaction.response.send_modal(modal)
    except Exception as e:
        logger.error(f"Error in main_sheet_edit_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-create",
    description="It will create or update leaderboard",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_create_command(
    interaction: discord.Interaction, spreadsheet_id: str = None, date: str = None
):
    global spread_sheet_id
    try:
        await interaction.response.defer()
        channel = interaction.channel

        if date:
            year, month = date.split("-")
        else:
            now = datetime.now()
            formatted_date = now.strftime("%Y-%m")
            year, month = formatted_date.split("-")

        leaderboard = create_leaderboard_by_month(year, month)
        create_leaderboard_sheet(
            spreadsheet_id or spread_sheet_id, leaderboard, year, month
        )
        messages = format_leaderboard_for_discord(leaderboard)
        for msg in messages:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        logger.error(f"Error in leaderboard_create_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-view",
    description="It will show leaderboard in the discord channel",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_view_command(
    interaction: discord.Interaction, thread_id: str, date: str = None
):
    await interaction.response.defer()
    channel = interaction.channel

    try:
        thread = await interaction.guild.fetch_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            raise ValueError("The provided ID does not belong to a thread.")

        if date:
            year, month = date.split("-")
        else:
            now = datetime.now()
            formatted_date = now.strftime("%Y-%m")
            year, month = formatted_date.split("-")

        leaderboard = create_leaderboard_by_month(year, month)
        messages = format_leaderboard_for_discord(leaderboard)

        bot_user_id = interaction.client.user.id
        async for message in thread.history(limit=None):
            if message.author.id == bot_user_id:
                await message.delete()

        for msg in messages:
            await thread.send(msg)

        await interaction.followup.send(
            f"Posted to {thread_id} successfully.", ephemeral=True
        )

    except Exception as e:
        logger.error(f"Error in leaderboard_view_command: {e}")
        await interaction.followup.send(f"Please check your input: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-start-auto-post",
    description="It will automatically post the leaderboard every day at a specified time",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_start_auto_post_command(
    interaction: discord.Interaction, date: str, time: str, spreadsheet_id: str = None
):
    global auto_post_task, task_details
    try:
        await interaction.response.defer()
        channel = interaction.channel

        year, month = date.split("-")
        hour, minute = map(int, time.split(":"))

        task_id = f"{year}-{month}"

        task_details[task_id] = {
            "year": year,
            "month": month,
            "spreadsheet_id": spreadsheet_id or spread_sheet_id,
            "hour": hour,
            "minute": minute,
            "channel": channel,
        }

        if not task_details[task_id]["spreadsheet_id"]:
            await interaction.followup.send(
                f"Spreadsheet id is missing; it will not update the spreadsheet!",
                ephemeral=True,
            )

        if task_id not in auto_post_tasks or not auto_post_tasks[task_id].is_running():
            auto_post_tasks[task_id] = tasks.loop(minutes=1)(
                auto_post_leaderboard(task_id)
            )
            auto_post_tasks[task_id].start()

        await interaction.followup.send(
            f"Auto-post leaderboard task started for {date} at {time}.", ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in leaderboard_start_auto_post_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-stop-auto-post",
    description="It will stop the auto-post leaderboard task for a specific date (YYYY-MM)",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_stop_auto_post_command(
    interaction: discord.Interaction, date: str
):
    try:
        await interaction.response.defer()

        if date in auto_post_tasks and auto_post_tasks[date].is_running():
            auto_post_tasks[date].cancel()
            await interaction.followup.send(
                f"Auto-post leaderboard task stopped for {date}.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"No auto-post leaderboard task is currently running for {date}.",
                ephemeral=True,
            )
    except Exception as e:
        logger.error(f"Error in leaderboard_stop_auto_post_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


def auto_post_leaderboard(task_id):
    async def inner():
        try:
            now = datetime.now()
            details = task_details[task_id]
            if now.hour == details["hour"] and now.minute == details["minute"]:
                leaderboard = create_leaderboard_by_month(
                    details["year"], details["month"]
                )
                create_leaderboard_sheet(
                    details["spreadsheet_id"],
                    leaderboard,
                    details["year"],
                    details["month"],
                )
                messages = format_leaderboard_for_discord(leaderboard)
                channel = details["channel"]
                bot_user_id = client.user.id
                async for message in channel.history(limit=None):
                    if message.author.id == bot_user_id:
                        await message.delete()
                for msg in messages:
                    await channel.send(msg)
        except Exception as e:
            logger.error(f"Error in auto_post_leaderboard task {task_id}: {e}")

    return inner


@tree.command(
    name="leaderboard-closure-month",
    description="It will create forum thread for leaderboard in the discord forum channel",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_closure_month_command(
    interaction: discord.Interaction, date: str = None, commit_filter: int = 10
):
    await interaction.response.defer()
    channel = interaction.channel

    try:
        forum_channel_id = int(config.LEADERBOARD_FORUM_CHANNEL_ID)
        forum_channel = interaction.guild.get_channel(forum_channel_id)
        if date:
            year, month = date.split("-")
            date_obj = datetime.strptime(f"{year}-{month}", "%Y-%m")
        else:
            now = datetime.now()
            date_obj = now
            formatted_date = now.strftime("%Y-%m")
            year, month = formatted_date.split("-")

        leaderboard = create_leaderboard_by_month(year, month, commit_filter)
        messages = format_leaderboard_for_discord(leaderboard, date, True)
        month_name = date_obj.strftime("%B")

        thread_title = f"Leaderboard | {year} {month_name}"
        thread, _ = await forum_channel.create_thread(
            name=thread_title, content=messages[0]
        )

        if len(messages) > 0:
            for msg in messages[1:]:
                await thread.send(msg)

        file_path = "user_data.csv"
        result = write_users_to_csv_monthly(file_path, date)

        if "Successfully" in result:
            await thread.send(file=discord.File(file_path))
            os.remove(file_path)

        await interaction.followup.send(
            f"Leaderboard thread created: {thread.jump_url}", ephemeral=True
        )

    except Exception as e:
        logger.error(f"Error in leaderboard_closure_month_command: {e}")
        await interaction.followup.send(f"Please check your input: {e}", ephemeral=True)


@tree.command(
    name="get-monthly-streaks",
    description="Gets monthly streaks of users and sends it to channel.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_monthly_streaks_command(
    interaction: discord.Interaction, date: str = None
):
    try:
        await interaction.response.defer()

        forum_channel_id = int(config.LEADERBOARD_FORUM_CHANNEL_ID)
        forum_channel = interaction.guild.get_channel(forum_channel_id)
        if date:
            year, month = date.split("-")
            date_obj = datetime.strptime(f"{year}-{month}", "%Y-%m")
        else:
            now = datetime.now()
            date_obj = now
            formatted_date = now.strftime("%Y-%m")
            year, month = formatted_date.split("-")
            date = f"{year}-{month}"

        month_name = date_obj.strftime("%B")
        streaks = calculate_monthly_streak(date)

        messages = format_streaks_for_discord(streaks, month_name)
        thread_title = f"Streaks | {year} {month_name}"
        thread, _ = await forum_channel.create_thread(
            name=thread_title, content=messages[0]
        )

        if len(messages) > 0:
            for msg in messages[1:]:
                await thread.send(msg)

        await interaction.followup.send(
            f"Streaks thread created: {thread.jump_url}", ephemeral=True
        )

    except Exception as e:
        logger.error(f"Error in get_monthly_streaks_command: {e}")
        await interaction.followup.send(f"Please check your input: {e}", ephemeral=True)


@tree.command(
    name="get-members-and-insert-to-db",
    description="Get and insert all members of the guild to the db in new collection",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_members_and_insert_to_db_command(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = interaction.channel

    try:
        members = interaction.guild.members
        member_list = [{member.name: member.id} for member in members]
        logger.info(member_list)
        result = insert_discord_users(member_list)
        if result:
            await interaction.followup.send(f"Users successfully inserted")
    except Exception as e:
        logger.error(f"An error occurred in get_members_and_insert_to_db_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


def convert_to_iso8601(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    iso8601_str = date_obj.strftime("%Y-%m-%dT%H:%M:%SZ")

    return iso8601_str


async def fetch(session, url, method="GET", data=None, params=None):
    async with session.request(method, url, json=data, params=params) as response:
        return await response.json()


# Run the bot
client.run(config.DISCORD_TOKEN)
