import os, json, re, logging

logger = logging.getLogger(__name__)

# tg-reviewer-bot-v2.1
if os.path.exists("./config.json"):
    data_dir = "./"
elif os.path.exists("/etc/tg_reviewer_bot/config.json"):
    data_dir = "/etc/tg_reviewer_bot/"
elif os.path.exists(os.path.expandvars(r"%PROGRAMDATA%\\tg_reviewer_bot\\config.json")):
    data_dir = "%PROGRAMDATA%\\tg_reviewer_bot\\"
else:
    data_dir = None

if data_dir is not None:
    try:
        with open((data_dir + "config.json"), 'r', encoding='utf-8') as f:
            data = json.load(f)
            logger.info("Read config file, JSON config file mode.")

            # required settings
            if data['required'] is None:
                raise ValueError("Config file incorrect: \"required\" is missing.")

            TG_TOKEN                    = data['required']['TG_TOKEN']
            TG_BOT_USERNAME             = data['required']['TG_BOT_USERNAME']   # For set message filter

            TG_REVIEWER_GROUP           = str(int(
                                          data['required']['TG_REVIEWER_GROUP']))

            TG_PUBLISH_CHANNEL_JSON     = data['required']['TG_PUBLISH_CHANNEL']
            TG_PUBLISH_CHANNEL          = [int(x) for x in TG_PUBLISH_CHANNEL_JSON]

            # non-required settings
            TG_CUSTOMAPI                = data.get('non_required', {}).get('TG_CUSTOMAPI', "https://api.telegram.org/bot")
            TG_DB_URL                   = data.get('non_required', {}).get('TG_DB_URL', "sqlite://")
            TG_REVIEWONLY               = data.get('non_required', {}).get('TG_REVIEWONLY', False)

            TG_REJECTED_CHANNEL         = data.get('non_required', {}).get('TG_REJECTED_CHANNEL', None)

            APPROVE_NUMBER_REQUIRED     = int(str(data.get('non_required', {}).get('APPROVE_NUMBER_REQUIRED', 2)))
            REJECT_NUMBER_REQUIRED      = int(str(data.get('non_required', {}).get('REJECT_NUMBER_REQUIRED', 2)))
            TG_TIMEOUT_SINGLEREVIEW     = int(str(data.get('non_required', {}).get('TG_TIMEOUT_SINGLEREVIEW', "10080")))

            TG_SINGLE_MODE              = data.get('non_required', {}).get('TG_SINGLE_MODE', True)

            TG_TEXT_SPOILER             = data.get('non_required', {}).get('TG_TEXT_SPOILER', True)
            TG_EXPAND_LENGTH            = int(str(data.get('non_required', {}).get('TG_EXPAND_LENGTH', 200)))

            TG_SELF_APPROVE             = data.get('non_required', {}).get('TG_SELF_APPROVE', True)
            TG_REJECT_REASON_USER_LIMIT = data.get('non_required', {}).get('TG_REJECT_REASON_USER_LIMIT', True)
            REJECTION_REASON_JSON       = data.get('non_required', {}).get('TG_REJECTION_REASON', ["已有其他相似投稿","内容不够有趣","内容过于火星","引起感官不适","内容 NSFW","没有 Get 到梗","不在可接受范围内","点错了，正在召唤补发"])
            REJECTION_REASON            = REJECTION_REASON_JSON if isinstance(REJECTION_REASON_JSON, list) else ["默认理由"]

            TG_RETRACT_NOTIFY           = data.get('non_required', {}).get('TG_RETRACT_NOTIFY', True)
            TG_BANNED_NOTIFY            = data.get('non_required', {}).get('TG_BANNED_NOTIFY', True)

    except (json.JSONDecodeError) as e:
        raise EnvironmentError("Warning: Config JSON found decode failed!")
    except (PermissionError) as e:
        raise EnvironmentError("Warning: Failed to read JSON file permission denied!")
else:
    try:
        logger.info(f"Warning: Failed to read config file, trying environment variable...")

        # get args from environment virables
        # required settings
        TG_TOKEN                        = os.environ["TG_TOKEN"]
        TG_REVIEWER_GROUP               = os.environ["TG_REVIEWER_GROUP"]
        TG_BOT_USERNAME                 = os.environ["TG_BOT_USERNAME"]

        try:
            getenvkey                   = os.environ["TG_PUBLISH_CHANNEL"]
            if getenvkey is None:
                raise ValueError("Environment variable 'TG_PUBLISH_CHANNEL' not found!")
            TG_PUBLISH_CHANNEL          = getenvkey.split(":")
        except KeyError:
            raise ValueError("Environment variable 'TG_PUBLISH_CHANNEL' not found!")

        # non-required settings
        # bool
        TG_SINGLE_MODE                  = os.getenv("TG_SINGLE_MODE", "True")       == "True"
        TG_TEXT_SPOILER                 = os.getenv("TG_TEXT_SPOILER", "True")      == "True"
        TG_SELF_APPROVE                 = os.getenv("TG_SELF_APPROVE", "True")      == "True"
        TG_RETRACT_NOTIFY               = os.getenv("TG_RETRACT_NOTIFY", "True")    == "True"
        TG_BANNED_NOTIFY                = os.getenv("TG_BANNED_NOTIFY", "True")     == "True"
        TG_REJECT_REASON_USER_LIMIT     = os.getenv("TG_REJECT_REASON_USER_LIMIT", "True") == "True"
        TG_REVIEWONLY                   = os.getenv("TG_REVIEWONLY", "False")       == "True"

        # int
        try:
            APPROVE_NUMBER_REQUIRED     = int(os.getenv("TG_APPROVE_NUMBER_REQUIRED", 2))
        except (TypeError, ValueError):
            APPROVE_NUMBER_REQUIRED     = 2

        try:
            REJECT_NUMBER_REQUIRED      = int(os.getenv("TG_REJECT_NUMBER_REQUIRED", 2))
        except (TypeError, ValueError):
            REJECT_NUMBER_REQUIRED      = 2

        try:
            TG_EXPAND_LENGTH            = int(os.getenv("TG_EXPAND_LENGTH", 200))
        except (TypeError, ValueError):
            TG_EXPAND_LENGTH            = 200

        try:
            TG_TIMEOUT_SINGLEREVIEW     = int(os.getenv("TG_TIMEOUT_SINGLEREVIEW", "10080"))
        except (TypeError, ValueError):
            TG_TIMEOUT_SINGLEREVIEW     = 200

        # string
        TG_REJECTED_CHANNEL             = os.environ.get("TG_REJECTED_CHANNEL")
        REJECTION_REASON                = os.environ.get("TG_REJECTION_REASON", "已有其他相似投稿:内容不够有趣:内容过于火星:引起感官不适:内容 NSFW:没有 Get 到梗:不在可接受范围内:点错了，正在召唤补发").split(":")
        TG_DB_URL                       = os.environ.get("TG_DB_URL", "sqlite:///data/database.db")
        TG_CUSTOMAPI                    = os.environ.get("TG_CUSTOMAPI", "https://api.telegram.org/bot")
    except KeyError as e:
        logger.exception("Env config error")
        raise