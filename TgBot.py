import logging
from typing import Dict

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Firebase
import firebase_admin
from firebase_admin import credentials, db

# ------------------ Configuration (edit these) ------------------
TOKEN = "Telegram bot token id"
SERVICE_ACCOUNT_PATH = r"Service Account Path"  # change to your key path
DATABASE_URL = "Database URL"  # change to your DB URL
# ----------------------------------------------------------------

# Conversation states
GET_PRN, GET_NAME, GET_PHONE, CONFIRM = range(4)

# Initialize logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# Initialize Firebase (safe to call once)
def init_firebase():
    try:
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        logger.info("Firebase initialized.")
    except Exception as e:
        # If already initialized in same process, ignore the error
        if "already exists" in str(e):
            logger.info("Firebase app already initialized.")
        else:
            logger.exception("Failed to initialize Firebase: %s", e)
            raise


# Top-level DB references (created after firebase init)
student_ref = None
applications_ref = None


def setup_db_refs():
    global student_ref, applications_ref
    student_ref = db.reference("Students")
    applications_ref = db.reference("bonafide_applications")


# ------------------ Bot handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the main inline menu when /start is called."""
    keyboard = [
        [InlineKeyboardButton("First Year", callback_data="first_year")],
        [InlineKeyboardButton("Second Year", callback_data="second_year")],
        [InlineKeyboardButton("Third Year", callback_data="third_year")],
        [InlineKeyboardButton("New Admission", callback_data="new_admission")],
        [InlineKeyboardButton("Admin Office", callback_data="admin_office")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Select your year:", reply_markup=reply_markup)
    else:
        # In case start called in non-standard way
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Select your year:", reply_markup=reply_markup)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("I can help you with university tasks (menus). Use /start to open the menu.")


async def button_click_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Generic handler for inline keyboard clicks outside the ConversationHandler flows.
    It routes the user based on callback_data.
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()  # acknowledge the callback

    data = query.data

    # Simple routing for demo menus
    if data == "first_year":
        keyboard = [
            [InlineKeyboardButton("Academic", callback_data="academic")],
            [InlineKeyboardButton("Back", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            text="Shows the menu for first-year students.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "new_admission":
        info_text = (
            "This is admission details. Steps and documents required:\n"
            "1. Step one...\n2. Step two...\n\nIf you need more help, contact the admin office."
        )
        await query.edit_message_text(text=info_text)

    elif data == "admin_office":
        # Admin office menu includes Bonafide button which will start a ConversationHandler
        keyboard = [
            [InlineKeyboardButton("Fee Receipt", callback_data="fee_receipt")],
            [InlineKeyboardButton("Bonafide", callback_data="start_bonafide_flow")],
            [InlineKeyboardButton("Admission Details", callback_data="admission_details")],
            [InlineKeyboardButton("Back", callback_data="first_year")],
        ]
        await query.edit_message_text(
            text="Shows the menu for admin office tasks.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "main_menu":
        # Go back to the top menu
        await query.edit_message_text(text="Use /start to open main menu again.")

    else:
        # Default response if not implemented
        await query.edit_message_text(text=f"You selected: {data} (This menu is not yet built)")


# ------------------ Conversation flow: Bonafide application ------------------

async def start_bonafide_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for the bonafide application conversation.
    This is triggered by a CallbackQuery with callback_data 'start_bonafide_flow'.
    """
    # update here is a CallbackQuery update (EntryPoint of ConversationHandler)
    query = update.callback_query
    if query:
        await query.answer()
        # Save chat id if needed later
        context.user_data["chat_id"] = query.message.chat_id
        await query.edit_message_text(text="Please enter your 8-digit PRN Number:")
        return GET_PRN

    # If somehow called by message, ask user to click the button
    if update.message:
        await update.message.reply_text("Please click the Bonafide button from the admin office menu.")
    return ConversationHandler.END


async def get_prn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle PRN input, validate against Firebase Students reference (8 digits)."""
    # Raw text the user sent
    raw = update.message.text
    prn = raw.strip()  # remove leading/trailing whitespace

    # Debug logging (will appear in your terminal)
    logger.info("Received raw PRN input (repr): %r", raw)
    logger.info("Normalized PRN: %r (len=%d, isdigit=%s)", prn, len(prn), prn.isdigit())

    # Extra safety: convert fullwidth/unicode digits to ascii digits if any (optional)
    # This ensures inputs like '１２３４５６７８' get normalized to '12345678'
    try:
        prn_ascii = "".join(ch for ch in prn if ch.isascii())
        if prn_ascii != prn:
            # fallback: try to translate numeric unicode digits to ascii using unicodedata
            import unicodedata
            converted = []
            for ch in prn:
                try:
                    d = unicodedata.digit(ch)
                    converted.append(str(d))
                except (TypeError, ValueError):
                    converted.append(ch)
            prn = "".join(converted)
            logger.info("Converted PRN to ASCII digits: %r", prn)
    except Exception:
        # if anything in conversion fails, we continue with original prn
        pass

    # VALIDATION: PRN must be digits and 8 characters (adjust to match DB)
    if not prn.isdigit() or len(prn) != 8:
        await update.message.reply_text(
            "Invalid PRN. Please enter your 8-digit PRN number (digits only) or type /cancel."
        )
        return GET_PRN

    # Query Firebase DB
    try:
        student_data = student_ref.child(prn).get()
        logger.info("Firebase lookup for PRN %s returned: %r", prn, student_data)
    except Exception as e:
        logger.exception("Firebase read error: %s", e)
        await update.message.reply_text("Database error. Please try again later.")
        return ConversationHandler.END

    if not student_data:
        # Helpful message with instructions and copy of current DB keys for debug (DO NOT expose to users in production)
        # We'll log the current top-level keys so you can compare — comment out if you don't want logs
        try:
            top_keys = list(student_ref.get(shallow=True).keys()) if student_ref.get(shallow=True) else []
        except Exception:
            top_keys = None
        logger.info("Top-level keys under Students (for debug): %r", top_keys)

        await update.message.reply_text(
            "Invalid PRN. That PRN is not found in our database. Please try again or type /cancel to exit."
        )
        return GET_PRN

    # Save retrieved student data and PRN
    context.user_data["prn"] = prn
    context.user_data["student_data"] = student_data

    name_prompt = (
        f"PRN Verified for: {student_data.get('name', '<name not available>')}\n\n"
        "Please enter your FULL NAME as per records in the format:\n"
        "SURNAME FIRST FATHER'S NAME"
    )
    await update.message.reply_text(name_prompt)
    return GET_NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate name against stored student record and move to phone."""
    name_input = update.message.text.strip().upper()
    correct_name = str(context.user_data["student_data"].get("name", "")).strip().upper()

    if name_input != correct_name:
        await update.message.reply_text(
            f"Invalid input. Name does not match database record ({correct_name}). Please try again or type /cancel."
        )
        return GET_NAME

    # Name valid
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Name verified. Please submit your 10-digit registered phone number.")
    return GET_PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate phone and ask for final confirmation."""
    phone_input = update.message.text.strip()
    correct_phone = str(context.user_data["student_data"].get("phone", "")).strip()

    if phone_input != correct_phone:
        await update.message.reply_text(
            "Invalid input. Phone number does not match our records. Please try again or type /cancel."
        )
        return GET_PHONE

    # All details valid. Save phone
    context.user_data["phone"] = phone_input

    # Build inline confirm keyboard
    keyboard = [
        [InlineKeyboardButton("Yes, Submit", callback_data="confirm_yes")],
        [InlineKeyboardButton("No, Cancel", callback_data="confirm_no")],
    ]
    await update.message.reply_text(
        "All details verified.\nAre you sure you want to submit the application for a bonafide certificate?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRM


async def confirm_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the final yes/no via CallbackQuery."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    # yes -> store to Firebase
    if query.data == "confirm_yes":
        try:
            student_data: Dict = context.user_data.get("student_data", {})
            application_data = {
                "prn": context.user_data.get("prn"),
                "name": context.user_data.get("name"),
                "phone": context.user_data.get("phone"),
                "batch": student_data.get("batch"),
                "status": "Pending",
                "submitted_at": str(query.message.date),
            }
            # push to database (generates unique id)
            applications_ref.push().set(application_data)
            await query.edit_message_text("Application submitted successfully. You will be notified when it is processed.")
        except Exception as e:
            logger.exception("Error submitting application: %s", e)
            await query.edit_message_text("An error occurred. Please try again later.")
        finally:
            return ConversationHandler.END

    else:
        # user clicked No
        await query.edit_message_text("Application cancelled.")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the conversation (can be invoked by /cancel at any time)."""
    # If this was a message cancel
    if update.message:
        await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    # If this was a callback query trying to cancel, acknowledge it
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Operation cancelled.")
    return ConversationHandler.END


# ------------------ Main ------------------

def main() -> None:
    # initialize firebase
    init_firebase()
    setup_db_refs()

    application = Application.builder().token(TOKEN).build()

    # ConversationHandler for bonafide flow
    conv_handler = ConversationHandler(
        entry_points=[
            # When button with callback_data 'start_bonafide_flow' is clicked start conversation
            CallbackQueryHandler(start_bonafide_flow, pattern="^start_bonafide_flow$"),
        ],
        states={
            GET_PRN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_prn)],
            GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            CONFIRM: [
                # confirmation handled by callbackquery (yes/no)
                CallbackQueryHandler(confirm_submission, pattern="^confirm_yes$"),
                CallbackQueryHandler(confirm_submission, pattern="^confirm_no$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
        ],
        per_user=True,
        per_chat=False,
        allow_reentry=False,
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    # general menu callback handler (for menu navigation and admin_office)
    application.add_handler(CallbackQueryHandler(button_click_handler, pattern="^(first_year|second_year|third_year|new_admission|admin_office|main_menu|fee_receipt|admission_details|academic)$"))
    # Add conversation handler (this includes its own CallbackQuery entrypoint for bonafide)
    application.add_handler(conv_handler)

    # Run the bot
    logger.info("Starting bot...")
    application.run_polling()


if __name__ == "__main__":
    main()