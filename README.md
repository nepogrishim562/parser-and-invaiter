# parser-and-invaiter
🕵️‍♂️ Parser + Inviter is an external tool for collecting and inviting Telegram members. I'll quickly gather active participants from any open chats and channels and invite them to your project. It's fully automated, secure, and has flexible settings.

📦 Installation
bash

pip install -r requirements.txt

🔑 Obtaining api_id and api_hash

Go to my.telegram.org and log in.

Open API Development Tools.

Create an app (any name) and copy the api_id (number) and api_hash (string).

🤖 Bot token (if used)

If the script is paired with a Telegram bot (for example, for control via chat), obtain the token from @BotFather and paste it into the appropriate place in the code.
⚙️ Account Configuration

The account must not have cloud two-factor authentication (2FA).
If 2FA is enabled, disable it in the Telegram settings before launching; otherwise, authorization will not go smoothly.

The bot password (line 47) is a required parameter and should not be confused with the account password.
In the code, line 47 defines the variable "password." This is an internal password required for the bot to function (for example, to access commands or to decrypt the session). Set it to a secure value. Without this password, the bot will not launch.

python

# Approximately line 47
password = "your_secret_password"

🚀 Launch

Insert your api_id and api_hash into the code.

Specify the password (line 47).

Run the script:

bash

python main.py

When you first log in, Telethon will ask for a phone number and a code sent via SMS (if the account doesn't have 2FA, this is sufficient). The session will be saved to a file; re-authorization will not be required.
⚠️ Important

Don't disclose the bot's api_id, api_hash, or password.

Use an account without 2FA so the script can log in automatically without unnecessary requests.

The password on line 47 is not your Telegram password, but a service key that makes the bot operational. Be sure to include it.
