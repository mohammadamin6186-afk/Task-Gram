# Task Gram — Full starter v1

This project implements the complete first version requested for Task Gram.

## Telegram bot flow

1. User opens the bot.
2. Bot checks membership in `@task_gram_game`.
3. If not joined, it shows:
   - Join Channel
   - Check Membership
4. After membership is confirmed:
   `send your Ton(Gram) address`
5. User submits a GRAM/TON address.
6. The bot shows the four-button menu:
   - 💸 Withdraw
   - 👥 Referral
   - 🎁 Earn More
   - 💰 Balance

## Balance

Shows:
`🤴User : <first name>`

`💰Your Balance : <balance> Gram`

and the requested restart message.

## Start again

`/start` never deletes balance or referral points.
It asks for the GRAM address again and replaces only the stored address.

## Referral

Every user has a link:
`https://t.me/Task_Gram_with_ads_bot?start=ref_<code>`

A new user arriving through that link gives the referrer 1 referral point.
Referral points are kept separately from the GRAM balance because the requested referral screen says "1 point" and describes a weekly winner.

## Withdraw

If balance < 0.5:
`⚠ Minimum Withdrawal Is 0.5 GRAM`

If balance >= 0.5:
`✅ Withdrawal Request Submitted`

`⏰ Time: 1 _ 24 h`

The request is stored and the balance is reserved/cleared. The admin receives:
- user id
- username/name
- GRAM address
- amount

Payment is manual.

## Earn More

The bot sends:
`🎯 Complete tasks & earn GRAM

Watch ADs and get rewarded instantly!`

with a Web App button:
`open tasks🚀`

## Mini App

Yellow background, white central card, large title:
`Watch ADs to earn GRAM rewards`

Reward display:
`+0.0005`
`GRAM`

One button:
`Watch AD to Claim📺`

There is deliberately no 10-ad counter, circle limit or percentage bar.

The interface itself has no daily ad limit. Actual ad availability/frequency can still be controlled by the ad network.

## AdsGram

The Mini App loads:
`https://sad.adsgram.ai/js/sad.min.js`

and initializes:
`window.Adsgram.init({ blockId: ADSGRAM_BLOCK_ID })`

A reward is recorded after AdsGram's Rewarded `show()` resolves.

IMPORTANT SECURITY NOTE:
AdsGram's client SDK tells the Mini App that the rewarded ad completed. A public web client can be modified by a malicious user, so a real-money production system should use any AdsGram server-side reward/confirmation mechanism available to the account, plus fraud/rate-limit controls. The starter also:
- validates Telegram `initData` cryptographically;
- creates a one-time ad session;
- makes each session claimable only once;
- rate-limits repeated reward claims.

This reduces abuse but is not a substitute for a provider-side server confirmation if one is available.

## Installation

1. Create a Python 3.11+ environment.
2. Install:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`.
4. Fill in the values.
5. Run:
   `python app.py`

For Telegram Mini Apps, `WEBAPP_URL` must be a public HTTPS URL.

## BotFather

After deploying:
BotFather -> your bot -> Main App -> URL:
`https://YOUR-DOMAIN.example/`

The URL must be the same public Mini App URL used in `.env`.

## AdsGram

After the Mini App exists, create/get the Rewarded Block ID in the AdsGram publisher dashboard and put it into:
`ADSGRAM_BLOCK_ID`

Do not put your bot token in HTML/JavaScript.

## Channel

The bot must be able to check membership in `@task_gram_game`. For reliable membership checking, add the bot to the channel as an administrator.

## Admin ID

The withdrawal notification is sent to `ADMIN_CHAT_ID`.
This must be your numeric Telegram user ID, not your @username.

## Database

SQLite is used for this starter. Tables:
- users
- ad_sessions
- ad_rewards
- withdrawals

For a larger production project, PostgreSQL is recommended.

## Important

The requested `0.0005 GRAM` is implemented as the application's reward value per completed rewarded ad. This does NOT mean AdsGram pays 0.0005 GRAM; your ad revenue and the user reward are separate economics.
