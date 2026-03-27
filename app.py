from flask import Flask, request, jsonify
import stripe
import discord
import os
import asyncio
import threading
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ================== 설정 ==================
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
B_SERVER_GUILD_ID = int(os.getenv('B_SERVER_GUILD_ID'))

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
        print("💰 결제 성공 이벤트 수신됨")

    return jsonify(success=True), 200


# ================== Success Page에서 호출할 핵심 엔드포인트 ==================
@app.route('/create-invite', methods=['POST'])
async def create_invite():
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "No session_id provided"}), 400

    try:
        # Stripe에서 session_id 검증 + 결제 상태 확인
        session = stripe.checkout.Session.retrieve(session_id)

        if session.payment_status != 'paid':
            return jsonify({"error": "Payment not completed"}), 400

        # 결제 검증 완료 → B서버에서 1인용 초대링크 생성
        guild = client.get_guild(B_SERVER_GUILD_ID)
        if not guild:
            return jsonify({"error": "B server not found"}), 404

        channel = guild.text_channels[0]   # 필요시 특정 채널 ID로 변경

        invite = await channel.create_invite(
            max_uses=1,        # 정확히 1회용
            unique=True,
            reason="Lifetime Payment"
        )

        return jsonify({
            "success": True,
            "invite_url": invite.url,
            "message": "Invite link generated successfully."
        })

    except stripe.error.StripeError as e:
        print(f"Stripe Error: {e}")
        return jsonify({"error": "Invalid or expired session"}), 400
    except Exception as e:
        print(f"Error creating invite: {e}")
        return jsonify({"error": "Failed to create invite link"}), 500


# ================== Flask + Discord Bot 실행 ==================
if __name__ == '__main__':
    # Discord Bot을 백그라운드 스레드로 실행
    def run_bot():
        asyncio.run(client.start(DISCORD_BOT_TOKEN))

    threading.Thread(target=run_bot, daemon=True).start()

    # Flask 실행
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
