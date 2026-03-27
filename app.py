from flask import Flask, request, jsonify
import stripe
import discord
import os
import asyncio
from dotenv import load_dotenv
import threading

load_dotenv()

app = Flask(__name__)

# ================== 설정 ==================
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
B_SERVER_GUILD_ID = int(os.getenv('B_SERVER_GUILD_ID'))   # B 서버 ID

# Discord Bot 클라이언트
intents = discord.Intents.default()
client = discord.Client(intents=intents)

# ================== Discord Bot ==================
@client.event
async def on_ready():
    print(f"✅ Discord Bot 로그인 완료 → {client.user}")

# ================== Stripe Webhook ==================
@app.route('/webhook', methods=['POST'])
async def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
    except Exception as e:
        print(f"Webhook Error: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        print("💰 결제 성공 이벤트 수신")
        # 여기서는 간단히 로그만 남김 (필요시 추가 로직)

    return jsonify(success=True), 200


# ================== Success Page에서 호출할 엔드포인트 ==================
@app.route('/create-invite', methods=['POST'])
async def create_invite():
    try:
        guild = client.get_guild(B_SERVER_GUILD_ID)
        if not guild:
            return jsonify({"error": "B 서버를 찾을 수 없습니다."}), 404

        # B서버에서 초대링크를 생성할 채널 (필요시 채널 ID로 변경 가능)
        channel = guild.text_channels[0]   # 첫 번째 텍스트 채널 사용

        invite = await channel.create_invite(
            max_uses=1,        # ★ 1인용 ★
            unique=True,
            reason="Lifetime Payment"
        )

        return jsonify({
            "success": True,
            "invite_url": invite.url,
            "message": "✅ 결제가 확인되었습니다!\nB서버 1회용 초대링크입니다."
        })

    except Exception as e:
        print(f"Invite creation error: {e}")
        return jsonify({"error": "초대링크 생성 중 오류가 발생했습니다."}), 500


# ================== Flask 실행 ==================
if __name__ == '__main__':
    # Discord Bot을 백그라운드에서 실행
    def run_bot():
        asyncio.run(client.start(DISCORD_BOT_TOKEN))

    threading.Thread(target=run_bot, daemon=True).start()

    # Flask 실행
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
