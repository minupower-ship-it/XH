from flask import Flask, request, jsonify
import stripe
import discord
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Stripe 설정
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# Discord 설정
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
B_SERVER_GUILD_ID = int(os.getenv('B_SERVER_GUILD_ID'))

@app.route('/webhook', methods=['POST'])
async def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
    except ValueError as e:
        print("Invalid payload")
        return jsonify(success=False), 400
    except stripe.error.SignatureVerificationError as e:
        print("Invalid signature")
        return jsonify(success=False), 400

    # 결제 성공 이벤트
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        discord_id = session['metadata'].get('discord_id')
        plan_type = session['metadata'].get('plan_type', 'lifetime')

        if discord_id:
            try:
                user = await client.fetch_user(int(discord_id))
                guild = client.get_guild(B_SERVER_GUILD_ID)
                
                if guild:
                    # B서버에서 1인용 초대링크 생성
                    channel = guild.text_channels[0]  # 첫 번째 채널 사용 (나중에 변경 가능)
                    invite = await channel.create_invite(
                        max_uses=1,
                        unique=True,
                        reason=f"Lifetime {plan_type} payment"
                    )
                    
                    await user.send(f"✅ 결제가 확인되었습니다!\n\nB서버 초대링크: {invite.url}\n\n링크는 1회용입니다. 바로 입장해주세요.")
                    print(f"초대링크 전송 완료: {discord_id} ({plan_type})")
                else:
                    print("B 서버를 찾을 수 없습니다.")
            except Exception as e:
                print(f"DM 전송 실패: {e}")

    return jsonify(success=True), 200

# Discord Bot 실행
@client.event
async def on_ready():
    print(f'✅ Discord Bot 로그인 완료: {client.user}')

if __name__ == '__main__':
    # Discord Bot은 별도 스레드로 실행 (간단 버전)
    import threading
    threading.Thread(target=client.run, args=(DISCORD_BOT_TOKEN,), daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
