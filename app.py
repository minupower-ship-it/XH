import os
import sqlite3
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import stripe
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ================== 환경변수 ==================
stripe.api_key            = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET            = os.getenv('STRIPE_WEBHOOK_SECRET')

# 서버 1 (xhouse.vip)
DISCORD_BOT_TOKEN         = os.getenv('DISCORD_BOT_TOKEN')
INVITE_CHANNEL_ID         = os.getenv('DISCORD_INVITE_CHANNEL_ID')
LIFETIME_PRICE_ID         = os.getenv('STRIPE_LIFETIME_PRICE_ID')
VIP_PRICE_ID              = os.getenv('STRIPE_VIP_PRICE_ID')
SUCCESS_URL_S1            = "https://xhouse.vip/success.html?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL_S1             = "https://xhouse.vip"

# 서버 2
S2_BOT_TOKEN              = os.getenv('S2_DISCORD_BOT_TOKEN')
S2_GUILD_ID               = os.getenv('S2_GUILD_ID')
S2_ROLE_ID                = os.getenv('S2_ROLE_ID')
S2_WEBHOOK_SECRET         = os.getenv('S2_STRIPE_WEBHOOK_SECRET')
S2_PRICE_ID               = os.getenv('S2_STRIPE_PRICE_ID')
SUCCESS_URL_S2            = "https://xhouse.vip/success2.html?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL_S2             = "https://xhouse.vip"

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
    conn.execute('''
        CREATE TABLE IF NOT EXISTS s2_payments (
            session_id   TEXT PRIMARY KEY,
            discord_id   TEXT,
            role_granted INTEGER DEFAULT 0,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ================== Discord 헬퍼 ==================
def discord_headers(token):
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

def create_discord_invite():
    url = f"{DISCORD_API}/channels/{INVITE_CHANNEL_ID}/invites"
    res = requests.post(url, headers=discord_headers(DISCORD_BOT_TOKEN), json={
        "max_uses": 1,
        "max_age": 0,
        "unique": True
    })
    res.raise_for_status()
    return f"https://discord.gg/{res.json()['code']}"

def assign_role(discord_id: str):
    """서버 2 유저에게 역할 부여"""
    url = f"{DISCORD_API}/guilds/{S2_GUILD_ID}/members/{discord_id}/roles/{S2_ROLE_ID}"
    res = requests.put(url, headers=discord_headers(S2_BOT_TOKEN))
    return res.status_code in (200, 204)

def send_dm(token: str, discord_id: str, message: str):
    """유저에게 DM 발송"""
    res = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        headers=discord_headers(token),
        json={"recipient_id": discord_id}
    )
    if res.status_code != 200:
        return False
    channel_id = res.json()["id"]
    res2 = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=discord_headers(token),
        json={"content": message}
    )
    return res2.status_code == 200

# ================== 서버 1: Checkout Session 생성 ==================
@app.route('/create-checkout', methods=['POST'])
def create_checkout():
    data = request.get_json()
    plan = data.get('plan')
    price_id = VIP_PRICE_ID if plan == 'vip' else LIFETIME_PRICE_ID

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='payment',
            success_url=SUCCESS_URL_S1,
            cancel_url=CANCEL_URL_S1,
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"[S1 Checkout] 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

# ================== 서버 1: Webhook ==================
@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        print(f"[S1 Webhook] 서명 검증 실패: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session_id = event['data']['object']['id']
        conn = sqlite3.connect('payments.db')
        c = conn.cursor()
        c.execute("SELECT session_id FROM payments WHERE session_id = ?", (session_id,))
        if not c.fetchone():
            c.execute("INSERT INTO payments (session_id) VALUES (?)", (session_id,))
            conn.commit()
            print(f"[S1 Webhook] 결제 저장: {session_id}")
        conn.close()

    return jsonify(success=True), 200

# ================== 서버 1: 초대링크 발급 ==================
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
    if existing_invite:
        conn.close()
        return jsonify({"success": True, "invite_url": existing_invite})

    try:
        invite_url = create_discord_invite()
        c.execute("UPDATE payments SET invite_url = ? WHERE session_id = ?", (invite_url, session_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "invite_url": invite_url})
    except Exception as e:
        conn.close()
        print(f"[S1 Invite] 생성 실패: {e}")
        return jsonify({"error": "초대링크 생성에 실패했습니다."}), 500

# ================== 서버 2: Checkout Session 생성 ==================
@app.route('/s2/create-checkout', methods=['POST'])
def s2_create_checkout():
    data       = request.get_json()
    discord_id = data.get('discord_id')

    if not discord_id:
        return jsonify({"error": "discord_id가 없습니다."}), 400

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': S2_PRICE_ID, 'quantity': 1}],
            mode='payment',
            success_url=SUCCESS_URL_S2,
            cancel_url=CANCEL_URL_S2,
            metadata={"discord_id": discord_id}
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"[S2 Checkout] 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

# ================== 서버 2: Webhook (역할 자동 부여) ==================
@app.route('/s2/webhook', methods=['POST'])
def s2_stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, S2_WEBHOOK_SECRET)
    except Exception as e:
        print(f"[S2 Webhook] 서명 검증 실패: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session    = event['data']['object']
        session_id = session['id']
        discord_id = session.get('metadata', {}).get('discord_id')

        if not discord_id:
            print(f"[S2 Webhook] discord_id 없음: {session_id}")
            return jsonify(success=True), 200

        conn = sqlite3.connect('payments.db')
        c    = conn.cursor()
        c.execute("SELECT session_id FROM s2_payments WHERE session_id = ?", (session_id,))
        if not c.fetchone():
            c.execute("INSERT INTO s2_payments (session_id, discord_id) VALUES (?, ?)", (session_id, discord_id))
            conn.commit()

            # 역할 부여
            success = assign_role(discord_id)
            if success:
                c.execute("UPDATE s2_payments SET role_granted = 1 WHERE session_id = ?", (session_id,))
                conn.commit()
                send_dm(S2_BOT_TOKEN, discord_id, "✅ 결제가 완료되었습니다! 멤버십이 활성화되었어요. 🎉")
                print(f"[S2 Webhook] 역할 부여 완료: {discord_id}")
            else:
                print(f"[S2 Webhook] 역할 부여 실패: {discord_id}")
        conn.close()

    return jsonify(success=True), 200

# ================== 헬스체크 ==================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ================== 실행 ==================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
