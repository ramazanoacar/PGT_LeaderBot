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


# Define Modal Classes
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
            thread = await interaction.guild.fetch_channel(int(thread_id))
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
            if "successfully" in result.lower():
                await interaction.channel.send(file=discord.File(file_path))
                os.remove(file_path)
                await interaction.followup.send(
                    "User monthly data is here:", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "User monthly data is not found.", ephemeral=True
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

            thread = await forum_channel.create_thread(
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

    @discord.ui.button(
        label="Get User Monthly Data to CSV",
        style=ButtonStyle.primary,
        custom_id="get_user_monthly_data_to_csv_1",  # Ensure unique custom_id
    )
    async def get_user_monthly_data_to_csv_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        modal = GetUserMonthlyDataModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Get Blockchain Summary",
        style=ButtonStyle.primary,
        custom_id="get_blockchain_summary_2",  # Ensure unique custom_id
    )
    async def get_blockchain_summary_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await interaction.response.defer(ephemeral=True)

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

    @discord.ui.button(
        label="Get All Data to CSV",
        style=ButtonStyle.secondary,
        custom_id="get_all_data_to_csv_3",  # Ensure unique custom_id
    )
    async def get_all_data_to_csv_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await interaction.response.defer(ephemeral=True)

            file_path = "all_data.csv"
            result = write_users_to_csv(file_path)
            if "successfully" in result.lower():
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

    @discord.ui.button(
        label="Get Monthly Streaks",
        style=ButtonStyle.success,
        custom_id="get_monthly_streaks_4",  # Ensure unique custom_id
    )
    async def get_monthly_streaks_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        modal = GetMonthlyStreaksModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Leaderboard View",
        style=ButtonStyle.success,
        custom_id="leaderboard_view_5",  # Ensure unique custom_id
    )
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

        # Fetch the admin channel
        admin_channel_id = int(
            config.TEST_ADMIN_CHANNEL_ID
        )  # Update with your admin channel ID
        admin_channel = client.get_channel(admin_channel_id)
        if admin_channel:
            # Delete existing button messages to prevent duplicates
            async for message in admin_channel.history(limit=100):
                if message.author == client.user:
                    if (
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
            await admin_channel.send(embed=embed, view=AdminCommandsView())
            logger.info("Admin commands view sent to admin channel.")
        else:
            logger.error(
                "Admin channel not found. Please check the TEST_ADMIN_CHANNEL_ID."
            )
    except Exception as e:
        logger.error(f"Error during on_ready: {e}")

    # Start the background task
    refresh_admin_buttons.start()


# Define a background task to periodically refresh the button message if needed
@tasks.loop(hours=1)
async def refresh_admin_buttons():
    try:
        admin_channel_id = int(
            config.TEST_ADMIN_CHANNEL_ID
        )  # Update with your admin channel ID
        admin_channel = client.get_channel(admin_channel_id)
        if admin_channel:
            # Delete existing button messages
            async for message in admin_channel.history(limit=100):
                if message.author == client.user:
                    if (
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
            await admin_channel.send(embed=embed, view=AdminCommandsView())
            logger.info("Refreshed admin commands view in admin channel.")
        else:
            logger.error(
                "Admin channel not found. Please check the TEST_ADMIN_CHANNEL_ID."
            )
    except Exception as e:
        logger.error(f"Error in refresh_admin_buttons task: {e}")


# Event: on_message
@client.event
async def on_message(message):
    try:
        if message.author == client.user:
            return
        # Add any additional on_message logic here if needed
    except Exception as e:
        logger.error(f"Error processing message: {e}")


# Define Slash Commands


@tree.command(
    name="commits-sheet-create",
    description="Create a Google Sheet with the contributions data.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def commits_sheet_create_command(
    interaction: discord.Interaction, spreadsheet_name: str, email_address: str = None
):
    global spread_sheet_id
    try:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        created_spreadsheet_id = create_new_spreadsheet(spreadsheet_name)

        share_spreadsheet(created_spreadsheet_id, email_address or config.GMAIL_ADDRESS)
        res = fill_created_spreadsheet_with_users_except_ai_decisions(
            created_spreadsheet_id
        )

        await interaction.followup.send(
            f"Spreadsheet created with ID: `{created_spreadsheet_id}` and name `{spreadsheet_name}`.\n"
            f"[View Spreadsheet](https://docs.google.com/spreadsheets/d/{created_spreadsheet_id})",
            ephemeral=True,
        )
        spread_sheet_id = created_spreadsheet_id
    except Exception as e:
        logger.error(f"Error in commits-sheet-create command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="commits-sheet-update",
    description="Update the Google Sheet with the updated contributions data.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def commits_sheet_update_command(
    interaction: discord.Interaction, spreadsheet_id: str
):
    global spread_sheet_id
    try:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        updated_spreadsheet_id = (
            update_created_spreadsheet_with_users_except_ai_decisions(spreadsheet_id)
        )

        await interaction.followup.send(
            f"Spreadsheet updated with ID: `{spread_sheet_id}`.\n"
            f"[View Spreadsheet](https://docs.google.com/spreadsheets/d/{spreadsheet_id})",
            ephemeral=True,
        )

        spread_sheet_id = updated_spreadsheet_id
    except Exception as e:
        logger.error(f"Error in commits-sheet-update command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="main-sheet-edit",
    description="Edit Google Sheets from Discord.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def main_sheet_edit_command(interaction: discord.Interaction, operation: str):
    try:
        if operation.lower() not in ["insert", "update", "add_repo", "delete"]:
            await interaction.followup.send(
                "Invalid operation. Please choose one of: insert, update, add_repo, delete.",
                ephemeral=True,
            )
            return

        modal = UserModal(operation=operation.lower())
        await interaction.response.send_modal(modal)
    except Exception as e:
        logger.error(f"Error in main_sheet_edit_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-create",
    description="Create or update the leaderboard.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_create_command(
    interaction: discord.Interaction, spreadsheet_id: str = None, date: str = None
):
    global spread_sheet_id
    try:
        await interaction.response.defer(ephemeral=True)
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
    description="Show the leaderboard in the specified Discord thread.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_view_command(
    interaction: discord.Interaction, thread_id: str, date: str = None
):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel

    try:
        thread = await interaction.guild.fetch_channel(int(thread_id))
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
            f"Posted to thread ID `{thread_id}` successfully.", ephemeral=True
        )

    except Exception as e:
        logger.error(f"Error in leaderboard_view_command: {e}")
        await interaction.followup.send(f"Please check your input: {e}", ephemeral=True)


@tree.command(
    name="leaderboard-closure-month",
    description="Create a forum thread for the leaderboard in the Discord forum channel.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def leaderboard_closure_month_command(
    interaction: discord.Interaction, date: str = None, commit_filter: int = 10
):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel

    try:
        forum_channel_id = int(config.LEADERBOARD_FORUM_CHANNEL_ID)
        forum_channel = interaction.guild.get_channel(forum_channel_id)
        if not forum_channel:
            raise ValueError("Leaderboard forum channel not found.")

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
        thread = await forum_channel.create_thread(
            name=thread_title, content=messages[0]
        )

        for msg in messages[1:]:
            await thread.send(msg)

        file_path = "user_data.csv"
        result = write_users_to_csv_monthly(file_path, date)

        if "successfully" in result.lower():
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
    description="Get monthly streaks of users and send them to a channel.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_monthly_streaks_command(
    interaction: discord.Interaction, date: str = None
):
    try:
        await interaction.response.defer(ephemeral=True)

        forum_channel_id = int(config.LEADERBOARD_FORUM_CHANNEL_ID)
        forum_channel = interaction.guild.get_channel(forum_channel_id)
        if not forum_channel:
            raise ValueError("Leaderboard forum channel not found.")

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
        thread = await forum_channel.create_thread(
            name=thread_title, content=messages[0]
        )

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
    description="Get and insert all members of the guild into the DB in a new collection.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_members_and_insert_to_db_command(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        members = interaction.guild.members
        member_list = [{member.name: member.id} for member in members]
        logger.info(member_list)
        result = insert_discord_users(member_list)
        if result:
            await interaction.followup.send(
                "Users successfully inserted.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "Failed to insert users into the database.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error in get_members_and_insert_to_db_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="run-task",
    description="Run the task for a specific timeframe.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def run_task_command(interaction: discord.Interaction, since: str, until: str):
    try:
        since_iso = convert_to_iso8601(since)
        until_iso = convert_to_iso8601(until)

        await interaction.response.defer(ephemeral=True)
        url = f"{config.GTP_ENDPOINT}/run-task"
        payload = {"since": since_iso, "until": until_iso}
        headers = {"Authorization": AUTH_TOKEN}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                response_data = await response.json()

        await interaction.followup.send(
            response_data.get("message", "Task completed."), ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in run_task_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="run-task-for-user",
    description="Run the task for a specific user with the specified timeframe.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def run_task_for_user_command(
    interaction: discord.Interaction, username: str, since: str, until: str
):
    try:
        since_iso = convert_to_iso8601(since)
        until_iso = convert_to_iso8601(until)

        await interaction.response.defer(ephemeral=True)
        url = f"{config.GTP_ENDPOINT}/run-task-for-user"
        payload = {"since": since_iso, "until": until_iso}
        params = {"username": username}
        headers = {"Authorization": AUTH_TOKEN}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, params=params, headers=headers
            ) as response:
                response_data = await response.json()

        await interaction.followup.send(
            response_data.get("message", "Task for user completed."), ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in run_task_for_user_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="control-scheduler",
    description="Control the scheduler (start/stop) with an optional interval.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def control_scheduler_command(
    interaction: discord.Interaction, action: str, interval: int = 1
):
    try:
        await interaction.response.defer(ephemeral=True)
        url = f"{config.GTP_ENDPOINT}/control-scheduler"
        payload = {"action": action.lower(), "interval_minutes": interval}
        headers = {"Authorization": AUTH_TOKEN}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                response_data = await response.json()

        await interaction.followup.send(
            response_data.get("message", "Scheduler action completed."), ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error in control_scheduler_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="get-ai-decisions-by-user",
    description="Get AI decisions as a CSV file for a specific user between given dates.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_ai_decisions_by_user_command(
    interaction: discord.Interaction, username: str, since: str, until: str
):
    try:
        await interaction.response.defer(ephemeral=True)
        ai_decisions = get_ai_decisions_by_user_and_timeframe(username, since, until)

        file_path = f"ai_decisions_by_user_{username}.csv"
        result = write_ai_decisions_to_csv(file_path, ai_decisions)
        if "successful" in result.lower():
            await interaction.channel.send(file=discord.File(file_path))
            os.remove(file_path)

            await interaction.followup.send("AI decisions here:", ephemeral=True)
        else:
            await interaction.followup.send(
                "AI decisions data not found or failed to write.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error in get_ai_decisions_by_user_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="get-all-data-to-csv",
    description="Get all DB data and export it to a CSV file.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_all_data_to_csv_command(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)

        file_path = "all_data.csv"
        result = write_users_to_csv(file_path)
        if "successfully" in result.lower():
            await interaction.channel.send(file=discord.File(file_path))
            os.remove(file_path)

            await interaction.followup.send("All data is here:", ephemeral=True)
        else:
            await interaction.followup.send(
                "Failed to retrieve all data.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error in get_all_data_to_csv_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="get-blockchain-summary",
    description="Get MINA Blockchain summary.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_blockchain_summary_command(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)

        url = "https://api.minaexplorer.com/summary"
        headers = {}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response_data = await response.json()

        res = json.dumps(response_data, indent=4)
        discord_message = f"```\n{res}\n```"

        await interaction.followup.send(discord_message, ephemeral=True)
    except Exception as e:
        logger.error(f"Error in get_blockchain_summary_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="get-user-monthly-data-to-csv",
    description="Get all DB data for a specific user for a month and export it to a CSV file.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def get_user_monthly_data_to_csv_command(
    interaction: discord.Interaction, username: str, date: str
):
    try:
        await interaction.response.defer(ephemeral=True)

        file_path = f"user_monthly_data_{username}_{date}.csv"
        result = write_all_data_of_user_to_csv_by_month(file_path, username, date)
        if "successfully" in result.lower():
            await interaction.channel.send(file=discord.File(file_path))
            os.remove(file_path)
            await interaction.followup.send(
                "User monthly data is here:", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "User monthly data is not found.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error in get_user_monthly_data_to_csv_command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@tree.command(
    name="delete-all-data",
    description="Delete all data between specific dates.",
    guild=discord.Object(id=config.GUILD_ID),
)
async def delete_all_data_command(
    interaction: discord.Interaction, from_date: str, until_date: str
):
    try:
        modal = UserDeletionModal(from_date=from_date, until_date=until_date)
        await interaction.response.send_modal(modal)
    except Exception as e:
        logger.error(f"Error in delete_all_data_command: {e}")
        await interaction.followup.send(
            "Something went wrong while processing the command.", ephemeral=True
        )


# Helper Functions


def convert_to_iso8601(date_str):
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        iso8601_str = date_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        return iso8601_str
    except ValueError as ve:
        logger.error(f"Date conversion error: {ve}")
        raise ValueError("Incorrect date format. Please use YYYY-MM-DD.")


async def fetch(session, url, method="GET", data=None, params=None):
    async with session.request(method, url, json=data, params=params) as response:
        return await response.json()


# Run the bot
client.run(config.DISCORD_TOKEN)
