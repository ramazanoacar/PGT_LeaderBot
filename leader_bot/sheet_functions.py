import os
import sys
import csv
import config

from typing import List
from log_config import get_logger
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from github_tracker_bot.mongo_data_handler import AIDecision
from config import GOOGLE_CREDENTIALS

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logger = get_logger(__name__)

SERVICE_ACCOUNT_FILE = GOOGLE_CREDENTIALS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
RANGE_NAME = "A1:D99999"


def get_google_sheets_service():
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        service = build("sheets", "v4", credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Failed to create Google Sheets service: {e}")


def get_google_drive_service():
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Failed to create Google Drive service: {e}")
        None


def read_sheet(spreadsheet_id):
    service = get_google_sheets_service()
    try:
        sheet = service.spreadsheets()
        result = (
            sheet.values().get(spreadsheetId=spreadsheet_id, range=RANGE_NAME).execute()
        )
        data = result.get("values", [])
        if not data:
            logger.debug("No data found.")
            return []

        return data

    except Exception as e:
        logger.error(f"Failed to read Google Sheets data: {e}")
        return []


def create_new_spreadsheet(title: str):
    service = get_google_sheets_service()
    try:
        spreadsheet = {"properties": {"title": title}}
        spreadsheet = (
            service.spreadsheets()
            .create(body=spreadsheet, fields="spreadsheetId")
            .execute()
        )
        logger.info(f"Spreadsheet created with ID: {spreadsheet.get('spreadsheetId')}")
        return spreadsheet.get("spreadsheetId")
    except Exception as e:
        logger.error(f"Failed to create new spreadsheet: {e}")
        return None


def create_leaderboard_sheet(
    spreadsheet_id: str, leaderboard: List[List[str]], year: str, month: str
):
    service = get_google_sheets_service()
    sheet_title = f"Leaderboard {year}-{month}"

    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_list = spreadsheet.get("sheets", [])

        sheet_exists = False
        for sheet in sheet_list:
            if sheet["properties"]["title"] == sheet_title:
                sheet_exists = True
                break

        if not sheet_exists:
            create_request = {"addSheet": {"properties": {"title": sheet_title}}}
            create_body = {"requests": [create_request]}
            response = (
                service.spreadsheets()
                .batchUpdate(spreadsheetId=spreadsheet_id, body=create_body)
                .execute()
            )

            new_sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]
            logger.info(f"Leaderboard sheet created with ID: {new_sheet_id}")
        else:
            logger.info(f"Sheet with title {sheet_title} already exists.")

        range_name = sheet_title
        value_range_body = {"range": range_name, "values": leaderboard}
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            body=value_range_body,
        ).execute()

        logger.info("Leaderboard data has been written to the sheet.")

    except Exception as e:
        logger.error(f"Failed to create leaderboard sheet: {e}")


def fill_created_spreadsheet_with_users_except_ai_decisions(spreadsheed_id):
    from db_functions import fetch_db_get_users

    try:
        column_names = [
            [
                "User Handle",
                "Github Name",
                "Repositories",
                "Total Daily Contribution Number",
                "Total Qualified Daily Contribution Number",
                "Qualified Daily Contribution Number by Month",
                "Qualified Daily Contribution Dates",
                "Best Streak",
            ]
        ]

        column_insert_result = insert_data(spreadsheed_id, "A1", column_names)

        users = fetch_db_get_users()
        data = []

        for user in users:
            data.append(
                [
                    user.user_handle,
                    user.github_name,
                    ", ".join(user.repositories),
                    user.total_daily_contribution_number,
                    user.total_qualified_daily_contribution_number,
                    str(user.qualified_daily_contribution_number_by_month),
                    str(user.qualified_daily_contribution_dates),
                    user.qualified_daily_contribution_streak,
                ]
            )

        result = insert_data(spreadsheed_id, "A1", data)
        return result
    except Exception as e:
        logger.error(f"Failed to fill spreadsheet: {e}")


def write_users_to_csv(file_path):
    from db_functions import fetch_db_get_users

    try:
        column_names = [
            "User Handle",
            "Github Name",
            "Repositories",
            "Total Daily Contribution Number",
            "Total Qualified Daily Contribution Number",
            "Qualified Daily Contribution Number by Month",
            "Qualified Daily Contribution Dates",
            "Best Streak",
        ]

        users = fetch_db_get_users()
        data = []

        for user in users:
            data.append(
                [
                    user.user_handle,
                    user.github_name,
                    ", ".join(user.repositories),
                    user.total_daily_contribution_number,
                    user.total_qualified_daily_contribution_number,
                    str(user.qualified_daily_contribution_number_by_month),
                    str(user.qualified_daily_contribution_dates),
                    user.qualified_daily_contribution_streak,
                ]
            )

        with open(file_path, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(column_names)
            writer.writerows(data)

        return f"Data successfully written to {file_path}"
    except Exception as e:
        logger.error(f"Failed to write to CSV: {e}")
        return f"Failed to write to CSV: {e}"


def write_users_to_csv_monthly(file_path, month):
    from db_functions import fetch_db_get_users

    try:
        users = fetch_db_get_users()
        filtered_users = []

        for user in users:
            if month in user.qualified_daily_contribution_number_by_month:
                user_data = user.to_dict()
                user_data["qualified_daily_contributions_in_month"] = (
                    user.qualified_daily_contribution_number_by_month[month]
                )
                filtered_users.append(user_data)

        if not filtered_users:
            logger.info("No users with contributions for the specified month")
            return "No users with contributions for the specified month"

        keys = filtered_users[0].keys()
        with open(file_path, "w", newline="") as output_file:
            dict_writer = csv.DictWriter(output_file, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(filtered_users)

        logger.info(f"Successfully wrote to {file_path}")
        return f"Successfully wrote to {file_path}"

    except Exception as e:
        logger.error(f"Failed to write to CSV: {e}")
        return f"Failed to write to CSV: {e}"


def write_ai_decisions_to_csv(
    file_path: str, ai_decisions: List[List[AIDecision]]
) -> str:
    try:
        with open(file_path, mode="w", newline="", encoding="utf-8") as csvfile:
            if not ai_decisions or not ai_decisions[0]:
                raise ValueError("Empty ai_decisions list")

            flat_decisions = [
                decision for sublist in ai_decisions for decision in sublist
            ]

            fieldnames = list(flat_decisions[0].to_dict().keys()) + list(
                flat_decisions[0].response.to_dict().keys()
            )

            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for decision in flat_decisions:
                decision_dict = decision.to_dict()
                row = {
                    **{k: v for k, v in decision_dict.items() if k != "response"},
                    **decision_dict["response"],
                }
                writer.writerow(row)

        return "CSV writing successful"
    except Exception as e:
        logger.error(f"Failed to write to CSV: {e}")
        return f"Failed to write to CSV: {e}"


def update_created_spreadsheet_with_users_except_ai_decisions(spreadsheed_id):
    from db_functions import fetch_db_get_users

    try:
        users = fetch_db_get_users()
        data = []

        for user in users:
            data.append(
                [
                    user.user_handle,
                    user.github_name,
                    ", ".join(user.repositories),
                    user.total_daily_contribution_number,
                    user.total_qualified_daily_contribution_number,
                    str(user.qualified_daily_contribution_number_by_month),
                    str(user.qualified_daily_contribution_dates),
                    user.qualified_daily_contribution_streak,
                ]
            )

        result = update_data(spreadsheed_id, "A2", data)
        return result
    except Exception as e:
        logger.error(f"Failed to fill spreadsheet: {e}")


def share_spreadsheet(spreadsheet_id: str, email: str):
    drive_service = get_google_drive_service()
    if drive_service is None:
        logger.error("Google Sheets service is not available.")
        return None
    try:
        permissions = [{"type": "user", "role": "writer", "emailAddress": email}]
        for permission in permissions:
            drive_service.permissions().create(
                fileId=spreadsheet_id,
                body=permission,
                supportsAllDrives=True,
            ).execute()
        logger.info(f"Spreadsheet {spreadsheet_id} shared with {email}")
    except Exception as e:
        logger.error(f"Failed to share spreadsheet: {e}")


# Example usage
# print(format_for_discord(read_sheet(config.SPREADSHEET_ID)))
def format_for_discord(data: List[List[str]]) -> str:
    if not data:
        return "No data found."

    headers = data[0]
    rows = data[1:]

    header_emoji = "🔹"
    row_emoji = "➡️"

    formatted_message = f"{header_emoji} " + " | ".join(headers) + "\n"
    formatted_message += "➖" * (len(headers) * 8) + "\n"

    for row in rows:
        formatted_message += f"{row_emoji} " + " | ".join(row) + "\n"

    return formatted_message


def insert_data(spreadsheet_id, range_name, values):
    service = get_google_sheets_service()
    try:
        body = {"values": values}
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            )
            .execute()
        )
        logger.info(f"{result.get('updates').get('updatedCells')} cells appended.")
        return result
    except Exception as e:
        logger.error(f"Failed to insert data into Google Sheets: {e}")
        return None


def update_data(spreadsheet_id, range_name, values):
    service = get_google_sheets_service()
    try:
        body = {"values": values}
        result = (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body=body,
            )
            .execute()
        )
        logger.info(f"{result.get('updatedCells')} cells updated.")
        return result
    except Exception as e:
        logger.error(f"Failed to update data in Google Sheets: {e}")
        return None


def insert_user(discord_handle: str, github_name: str, repositories: List[str]):
    data = [[discord_handle, github_name, ", ".join(repositories).strip()]]
    insert_data(config.SPREADSHEET_ID, "A1", data)


def add_repository_for_user(discord_handle: str, repository_link: str):
    data = read_sheet(config.SPREADSHEET_ID)
    if not data:
        logger.error("No data found in the spreadsheet.")
        return

    updated_data = []
    user_found = False
    for row in data:
        if row[0] == discord_handle:
            user_found = True
            current_repos = row[2].split(", ")
            if repository_link not in current_repos:
                current_repos.append(repository_link)
            row[2] = ", ".join(current_repos)
        updated_data.append(row)

    if not user_found:
        logger.error(f"User with Discord handle {discord_handle} not found.")
        return

    update_data(config.SPREADSHEET_ID, RANGE_NAME, updated_data)


def update_user(
    discord_handle: str, new_github_name: str = None, new_repositories: List[str] = None
):
    data = read_sheet(config.SPREADSHEET_ID)
    if not data:
        logger.error("No data found in the spreadsheet.")
        return

    updated_data = []
    user_found = False
    for row in data:
        if row[0] == discord_handle:
            user_found = True
            if new_github_name is not None:
                row[1] = new_github_name
            if new_repositories is not None:
                row[2] = ", ".join(new_repositories).strip()
        updated_data.append(row)

    if not user_found:
        logger.error(f"User with Discord handle {discord_handle} not found.")
        return

    clear_range = "A1:Z"
    clear_request = {"range": clear_range}
    service = get_google_sheets_service()
    try:
        service.spreadsheets().values().clear(
            spreadsheetId=config.SPREADSHEET_ID, range=clear_range, body=clear_request
        ).execute()
    except Exception as e:
        logger.error(f"Failed to clear Google Sheets data: {e}")

    update_data(config.SPREADSHEET_ID, RANGE_NAME, updated_data)


def delete_user(discord_handle: str):
    data = read_sheet(config.SPREADSHEET_ID)
    if not data:
        logger.error("No data found in the spreadsheet.")
        return

    updated_data = []
    user_found = False
    for row in data:
        if row[0] != discord_handle:
            updated_data.append(row)
        else:
            user_found = True

    if not user_found:
        logger.error(f"User with Discord handle {discord_handle} not found.")
        return

    clear_range = "A1:Z"
    clear_request = {"range": clear_range}
    service = get_google_sheets_service()
    try:
        service.spreadsheets().values().clear(
            spreadsheetId=config.SPREADSHEET_ID, range=clear_range, body=clear_request
        ).execute()
    except Exception as e:
        logger.error(f"Failed to clear Google Sheets data: {e}")

    update_data(config.SPREADSHEET_ID, RANGE_NAME, updated_data)


def write_all_data_of_user_to_csv_by_month(file_path: str, username: str, date: str):
    from db_functions import get_ai_decisions_by_user_and_timeframe
    from helpers import get_monthly_user_data_from_ai_decisions, get_since_until_y_m_d

    try:
        since, until = get_since_until_y_m_d(date)
        ai_decisions = get_ai_decisions_by_user_and_timeframe(username, since, until)
        date_dict = get_monthly_user_data_from_ai_decisions(ai_decisions)
        if not date_dict:
            return "No data found for the specified month."

        with open(file_path, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                ["username", "date", "is_qualified", "total_qualified_so_far"]
            )
            total_qualified = 0
            for date, [nonqualified, qualified] in date_dict.items():
                qualified = True if qualified != 0 else False
                total_qualified += qualified
                writer.writerow(
                    [
                        username,
                        date,
                        qualified,
                        total_qualified,
                    ]
                )

        return "successfully"

    except Exception as e:
        logger.error(f"Failed to write to CSV: {e}")
        return f"Failed to write to CSV: {e}"


def get_repositories_from_user(username: str):
    data = read_sheet(config.SPREADSHEET_ID)
    if not data:
        logger.error("No data found in the spreadsheet.")
        return

    for row in data:
        if row[0] == username:
            return row[2].split(", ")

    logger.error(f"User with Discord handle {username} not found.")
    return None
