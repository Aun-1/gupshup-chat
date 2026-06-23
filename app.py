from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from PIL import Image
import os
import uuid
import html

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.secret_key = os.environ.get('SECRET_KEY', 'change-this-in-production-' + str(uuid.uuid4()))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'chat.db')
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt', 'zip'}

os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'profiles'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'media'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    profile_pic = db.Column(db.String(200), default='')


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    receiver = db.Column(db.String(80), nullable=False)
    text = db.Column(db.Text, nullable=False, default='')
    media_path = db.Column(db.String(200), default='')
    media_type = db.Column(db.String(20), default='')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)


class Block(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    blocker = db.Column(db.String(80), nullable=False)
    blocked = db.Column(db.String(80), nullable=False)


with app.app_context():
    db.create_all()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_blocked(user1, user2):
    return Block.query.filter(
        ((Block.blocker == user1) & (Block.blocked == user2)) |
        ((Block.blocker == user2) & (Block.blocked == user1))
    ).first() is not None


def save_profile_pic(file):
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = str(uuid.uuid4()) + '.' + ext
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'profiles', filename)
    img = Image.open(file)
    img = img.convert('RGB')
    img.thumbnail((300, 300), Image.LANCZOS)
    img.save(save_path, quality=90, optimize=True)
    return 'uploads/profiles/' + filename


def escape_text(t):
    return html.escape(t or '')


@app.route('/')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('contacts'))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'].strip()
        if not username:
            return render_template('signup.html', error='Username cannot be empty')
        existing = User.query.filter_by(username=username).first()
        if existing:
            return render_template('signup.html', error='Username already taken')
        password = generate_password_hash(request.form['password'])
        profile_pic = ''
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename and allowed_file(file.filename):
                profile_pic = save_profile_pic(file)
        user = User(username=username, password=password, profile_pic=profile_pic)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['username'] = username
            return redirect(url_for('contacts'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if request.method == 'POST':
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename and allowed_file(file.filename):
                user.profile_pic = save_profile_pic(file)
                db.session.commit()
    return render_template('profile.html', user=user)


def get_contacts_data(me, query):
    blocked_by_me = [b.blocked for b in Block.query.filter_by(blocker=me).all()]

    if query:
        users = User.query.filter(
            User.username.ilike(f'%{query}%'),
            User.username != me,
            ~User.username.in_(blocked_by_me)
        ).all()
    else:
        users = User.query.filter(
            User.username != me,
            ~User.username.in_(blocked_by_me)
        ).all()

    user_data = []
    for u in users:
        last_msg = Message.query.filter(
            ((Message.sender == me) & (Message.receiver == u.username)) |
            ((Message.sender == u.username) & (Message.receiver == me))
        ).order_by(Message.timestamp.desc()).first()
        unread = Message.query.filter_by(sender=u.username, receiver=me, is_read=False).count()
        user_data.append({
            'user': u,
            'last_msg': last_msg,
            'unread': unread,
        })

    user_data.sort(key=lambda x: x['last_msg'].timestamp if x['last_msg'] else datetime.min, reverse=True)
    return user_data


@app.route('/contacts')
def contacts():
    if 'username' not in session:
        return redirect(url_for('login'))
    query = request.args.get('search', '')
    me = session['username']
    user_data = get_contacts_data(me, query)
    return render_template('contacts.html', user_data=user_data, user=me, search=query)


@app.route('/api/contacts')
def api_contacts():
    if 'username' not in session:
        return jsonify([])
    query = request.args.get('search', '')
    me = session['username']
    user_data = get_contacts_data(me, query)

    result = []
    for item in user_data:
        u = item['user']
        last_msg = item['last_msg']
        last_msg_data = None
        if last_msg:
            if last_msg.text:
                last_msg_data = {'sender': last_msg.sender, 'kind': 'text', 'preview': last_msg.text[:40]}
            elif last_msg.media_type == 'image':
                last_msg_data = {'sender': last_msg.sender, 'kind': 'image', 'preview': ''}
            else:
                last_msg_data = {'sender': last_msg.sender, 'kind': 'file', 'preview': ''}
        result.append({
            'username': u.username,
            'profile_pic': u.profile_pic,
            'last_msg': last_msg_data,
            'unread': item['unread'],
        })
    return jsonify(result)


@app.route('/chat/<username>', methods=['GET', 'POST'])
def chat(username):
    if 'username' not in session:
        return redirect(url_for('login'))
    if is_blocked(session['username'], username):
        return redirect(url_for('contacts'))
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        media_path = ''
        media_type = ''
        if 'media' in request.files:
            file = request.files['media']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                original_name = secure_filename(file.filename)
                unique_prefix = str(uuid.uuid4())[:8]
                filename = unique_prefix + '_' + original_name
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'media', filename))
                media_path = 'uploads/media/' + filename
                media_type = 'image' if ext in {'png', 'jpg', 'jpeg', 'gif'} else 'file'
        if text or media_path:
            msg = Message(sender=session['username'], receiver=username,
                          text=text, media_path=media_path, media_type=media_type)
            db.session.add(msg)
            db.session.commit()
        return redirect(url_for('chat', username=username))

    Message.query.filter_by(sender=username, receiver=session['username'], is_read=False).update({'is_read': True})
    db.session.commit()

    messages = Message.query.filter(
        ((Message.sender == session['username']) & (Message.receiver == username)) |
        ((Message.sender == username) & (Message.receiver == session['username']))
    ).order_by(Message.timestamp).all()
    other_user = User.query.filter_by(username=username).first()
    i_blocked = Block.query.filter_by(blocker=session['username'], blocked=username).first() is not None
    return render_template('chat.html', messages=messages, user=session['username'],
                           other=username, other_user=other_user, i_blocked=i_blocked)


@app.route('/block/<username>')
def block(username):
    if 'username' not in session:
        return redirect(url_for('login'))
    existing = Block.query.filter_by(blocker=session['username'], blocked=username).first()
    if not existing:
        b = Block(blocker=session['username'], blocked=username)
        db.session.add(b)
        db.session.commit()
    return redirect(request.referrer or url_for('contacts'))


@app.route('/unblock/<username>')
def unblock(username):
    if 'username' not in session:
        return redirect(url_for('login'))
    Block.query.filter_by(blocker=session['username'], blocked=username).delete()
    db.session.commit()
    return redirect(request.referrer or url_for('contacts'))


@app.route('/blocked')
def blocked_list():
    if 'username' not in session:
        return redirect(url_for('login'))
    blocked_users = Block.query.filter_by(blocker=session['username']).all()
    users = [User.query.filter_by(username=b.blocked).first() for b in blocked_users]
    users = [u for u in users if u]
    return render_template('blocked.html', users=users)


@app.route('/api/messages/<username>')
def api_messages(username):
    if 'username' not in session:
        return jsonify([])
    since_id = request.args.get('since', 0, type=int)
    me = session['username']

    Message.query.filter_by(sender=username, receiver=me, is_read=False).update({'is_read': True})
    db.session.commit()

    q = Message.query.filter(
        ((Message.sender == me) & (Message.receiver == username)) |
        ((Message.sender == username) & (Message.receiver == me))
    )
    if since_id:
        q = q.filter(Message.id > since_id)
    messages = q.order_by(Message.timestamp).all()

    return jsonify([{
        'id': m.id,
        'sender': m.sender,
        'text': m.text,
        'media_path': m.media_path,
        'media_type': m.media_type,
        'timestamp': m.timestamp.strftime('%H:%M')
    } for m in messages])


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')