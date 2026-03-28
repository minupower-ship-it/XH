import os
import sqlite3
import requests
from flask import Flask, request, jsonify, redirect
import stripe
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ================== 환경변수 ==================
stripe.api_key        = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET        = os.getenv('STRIPE_WEBHOOK_SECRET')
DISCORD_BOT_TOKEN     = os.getenv('DISCORD_BOT_TOKEN')
INVITE_CHANNEL_ID     = os.getenv('DISCORD_INVITE_CHANNEL_ID')

# Stripe Price ID (대시보드 → Products → Price ID 복사)
LIFETIME_PRICE_ID     = os.getenv('STRIPE_LIFETIME_PRICE_ID')
VIP_PRICE_ID          = os.getenv('STRIPE_VIP_PRICE_ID')

SUCCESS_URL           = "https://xhouse.vip/success.html?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL            = "https://xhouse.vip"

DISCORD_API = "https://discord.com/api/v10"

# ================== DB 초기화 ==================
def init_db():
    conn = sqlite3.connect('payments.db')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            session_id  TEXT PRIMARY KEY,
            invite_url  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ================== Discord 헬퍼 ==================
def discord_headers():
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

def create_discord_invite():
    url = f"{DISCORD_API}/channels/{INVITE_CHANNEL_ID}/invites"
    res = requests.post(url, headers=discord_headers(), json={
        "max_uses": 1,
        "max_age": 0,
        "unique": True
    })
    res.raise_for_status()
    return f"https://discord.gg/{res.json()['code']}"

# ================== Stripe Checkout Session 생성 ==================
@app.route('/create-checkout', methods=['POST'])
def create_checkout():
    data = request.get_json()
    plan = data.get('plan')  # 'lifetime' 또는 'vip'

    if plan == 'vip':
        price_id = VIP_PRICE_ID
    else:
        price_id = LIFETIME_PRICE_ID

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='payment',
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"[Checkout] 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

# ================== Stripe Webhook ==================
@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        print(f"[Webhook] 서명 검증 실패: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session_id = event['data']['object']['id']

        conn = sqlite3.connect('payments.db')
        c = conn.cursor()
        c.execute("SELECT session_id FROM payments WHERE session_id = ?", (session_id,))
        if not c.fetchone():
            c.execute("INSERT INTO payments (session_id) VALUES (?)", (session_id,))
            conn.commit()
            print(f"[Webhook] 결제 저장 완료: {session_id}")
        conn.close()

    return jsonify(success=True), 200

# ================== 초대링크 발급 ==================
@app.route('/create-invite', methods=['POST'])
def create_invite():
    data       = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "session_id가 없습니다."}), 400

    conn = sqlite3.connect('payments.db')
    c    = conn.cursor()
    c.execute("SELECT invite_url FROM payments WHERE session_id = ?", (session_id,))
    row = c.fetchone()

    # DB에 없으면 Stripe에서 직접 검증 (Webhook 누락 대비)
    if not row:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status != 'paid':
                conn.close()
                return jsonify({"error": "결제가 완료되지 않았습니다."}), 400
            c.execute("INSERT INTO payments (session_id) VALUES (?)", (session_id,))
            conn.commit()
            row = (None,)
        except stripe.error.StripeError as e:
            conn.close()
            return jsonify({"error": f"결제 검증 실패: {str(e)}"}), 400

    existing_invite = row[0]

    # 이미 발급된 링크가 있으면 재사용
    if existing_invite:
        conn.close()
        return jsonify({"success": True, "invite_url": existing_invite})

    # 새 초대링크 생성
    try:
        invite_url = create_discord_invite()
        c.execute("UPDATE payments SET invite_url = ? WHERE session_id = ?", (invite_url, session_id))
        conn.commit()
        conn.close()
        print(f"[Invite] 발급 완료: {invite_url}")
        return jsonify({"success": True, "invite_url": invite_url})
    except Exception as e:
        conn.close()
        print(f"[Invite] 생성 실패: {e}")
        return jsonify({"error": "초대링크 생성에 실패했습니다. 잠시 후 다시 시도해주세요."}), 500

# ================== 헬스체크 ==================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ================== 실행 ==================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
