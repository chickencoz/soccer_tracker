from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'replace-with-your-secret-key'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'soccer.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Event types for outfield and keeper
OUTFIELD_TYPES = ('shot', 'goal', 'assist', 'pass')
KEEPER_TYPES = ('shot_on_goal_against', 'save', 'concede')

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    position = db.Column(db.String(20), nullable=False)  # 'field' or 'keeper'
    event_type = db.Column(db.String(40), nullable=False)
    # For shot events, we allow extra info (on_target, scored). Stored as booleans in columns for simplicity:
    on_target = db.Column(db.Boolean, default=False)
    scored = db.Column(db.Boolean, default=False)
    notes = db.Column(db.String(200), nullable=True)

    def __repr__(self):
        return f"<Event {self.position} {self.event_type} {self.timestamp}>"

class TrainingGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    metric = db.Column(db.String(50), nullable=False)  # e.g., "goals_per_week", "save_percentage"
    target = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Goal {self.metric} -> {self.target}>"

# Initialize DB helper
@app.before_first_request
def create_tables():
    db.create_all()

# Home / quick add links
@app.route('/')
def index():
    latest = Event.query.order_by(Event.timestamp.desc()).limit(6).all()
    return render_template('index.html', latest=latest)

# Add event form & processing
@app.route('/add', methods=['GET', 'POST'])
def add_event():
    if request.method == 'POST':
        position = request.form['position']  # 'field' or 'keeper'
        event_type = request.form['event_type']
        notes = request.form.get('notes', '').strip() or None
        on_target = request.form.get('on_target') == 'yes'
        scored = request.form.get('scored') == 'yes'

        # Basic validation
        if position not in ('field', 'keeper'):
            flash('Invalid position', 'danger')
            return redirect(url_for('add_event'))
        if position == 'field' and event_type not in OUTFIELD_TYPES:
            flash('Invalid outfield event type', 'danger')
            return redirect(url_for('add_event'))
        if position == 'keeper' and event_type not in KEEPER_TYPES:
            flash('Invalid keeper event type', 'danger')
            return redirect(url_for('add_event'))

        ev = Event(position=position, event_type=event_type, notes=notes,
                   on_target=on_target, scored=scored)
        db.session.add(ev)
        db.session.commit()
        flash('Event added!', 'success')
        return redirect(url_for('index'))

    return render_template('add_event.html', outfield_types=OUTFIELD_TYPES, keeper_types=KEEPER_TYPES)

# Stats page
@app.route('/stats')
def stats():
    events = Event.query.all()

    # Outfield calculations
    total_shots = sum(1 for e in events if e.position == 'field' and e.event_type == 'shot')
    total_on_target = sum(1 for e in events if e.position == 'field' and e.event_type == 'shot' and e.on_target)
    total_goals = sum(1 for e in events if e.position == 'field' and (e.event_type == 'goal' or (e.event_type == 'shot' and e.scored)))
    total_assists = sum(1 for e in events if e.position == 'field' and e.event_type == 'assist')
    total_passes = sum(1 for e in events if e.position == 'field' and e.event_type == 'pass')

    goal_percentage = (total_goals / total_shots * 100) if total_shots else None
    on_target_percentage = (total_on_target / total_shots * 100) if total_shots else None

    # Keeper calculations (shots on goal against, saves, save percentage)
    shots_on_against = sum(1 for e in events if e.position == 'keeper' and e.event_type == 'shot_on_goal_against')
    saves = sum(1 for e in events if e.position == 'keeper' and e.event_type == 'save')
    conceded = sum(1 for e in events if e.position == 'keeper' and e.event_type == 'concede')

    save_percentage = (saves / shots_on_against * 100) if shots_on_against else None
    goals_allowed = conceded

    # Training goals progress
    goals = TrainingGoal.query.all()
    goals_progress = []
    for g in goals:
        prog = compute_goal_progress(g.metric, g.target, events)
        goals_progress.append({'metric': g.metric, 'target': g.target, 'progress': prog})

    # For charting we prepare small series
    chart_data = {
        'labels': [],
        'goals': [],
        'shots': []
    }
    # simple time series by date (YYYY-MM-DD)
    from collections import defaultdict
    by_date = defaultdict(lambda: {'goals': 0, 'shots': 0})
    for e in events:
        day = e.timestamp.date().isoformat()
        if e.position == 'field' and (e.event_type == 'goal' or (e.event_type=='shot' and e.scored)):
            by_date[day]['goals'] += 1
        if e.position == 'field' and e.event_type == 'shot':
            by_date[day]['shots'] += 1
    dates = sorted(by_date.keys())
    chart_data['labels'] = dates
    chart_data['goals'] = [by_date[d]['goals'] for d in dates]
    chart_data['shots'] = [by_date[d]['shots'] for d in dates]

    return render_template('stats.html',
                           total_shots=total_shots,
                           total_on_target=total_on_target,
                           total_goals=total_goals,
                           total_assists=total_assists,
                           total_passes=total_passes,
                           goal_percentage=round(goal_percentage,2) if goal_percentage is not None else None,
                           on_target_percentage=round(on_target_percentage,2) if on_target_percentage is not None else None,
                           shots_on_against=shots_on_against,
                           saves=saves,
                           save_percentage=round(save_percentage,2) if save_percentage is not None else None,
                           goals_allowed=goals_allowed,
                           goals_progress=goals_progress,
                           chart_data=chart_data
                           )

def compute_goal_progress(metric, target, events):
    """
    A simple function that interprets a metric string and computes progress value.
    Supported metrics (examples):
      - goals_per_week
      - save_percentage
      - shots_per_training
    Returns a value that indicates current measured value (not percent complete).
    """
    from collections import defaultdict
    if metric == 'goals_per_week':
        # count goals in last 7 days
        cutoff = datetime.utcnow().date()
        last7 = [e for e in events if e.position == 'field' and (e.event_type == 'goal' or (e.event_type=='shot' and e.scored)) and (e.timestamp.date() >= (cutoff))]
        # Note: simple placeholder — you can replace with real date range logic
        return len(last7)
    if metric == 'save_percentage':
        shots_on = sum(1 for e in events if e.position=='keeper' and e.event_type=='shot_on_goal_against')
        saves = sum(1 for e in events if e.position=='keeper' and e.event_type=='save')
        return (saves / shots_on * 100) if shots_on else None
    if metric == 'shots_per_training':
        # naive average: total shots / number of sessions (not implemented: sessions)
        total_shots = sum(1 for e in events if e.position=='field' and e.event_type=='shot')
        # no session table, so we return total shots as fallback
        return total_shots
    # if unknown metric, return None
    return None

# Add / Remove training goals
@app.route('/goals', methods=['GET', 'POST'])
def goals():
    if request.method == 'POST':
        metric = request.form['metric'].strip()
        try:
            target = float(request.form['target'])
        except ValueError:
            flash('Target must be a number', 'danger')
            return redirect(url_for('goals'))
        g = TrainingGoal(metric=metric, target=target)
        db.session.add(g)
        db.session.commit()
        flash('Training goal added', 'success')
        return redirect(url_for('goals'))
    goals = TrainingGoal.query.order_by(TrainingGoal.created_at.desc()).all()
    return render_template('goals.html', goals=goals)

@app.route('/goal/delete/<int:goal_id>', methods=['POST'])
def delete_goal(goal_id):
    g = TrainingGoal.query.get_or_404(goal_id)
    db.session.delete(g)
    db.session.commit()
    flash('Goal removed', 'info')
    return redirect(url_for('goals'))

# API endpoint to fetch raw stats as JSON (useful for mobile or automation)
@app.route('/api/stats')
def api_stats():
    events = Event.query.all()
    stats = {
        'total_shots': sum(1 for e in events if e.position == 'field' and e.event_type == 'shot'),
        'total_goals': sum(1 for e in events if e.position == 'field' and (e.event_type == 'goal' or (e.event_type == 'shot' and e.scored))),
        'total_assists': sum(1 for e in events if e.position == 'field' and e.event_type == 'assist'),
        'keeper_saves': sum(1 for e in events if e.position == 'keeper' and e.event_type=='save'),
    }
    return jsonify(stats)

# small helper to clear all events (for testing) -- you can remove in production
@app.route('/admin/clear', methods=['POST'])
def admin_clear():
    Event.query.delete()
    TrainingGoal.query.delete()
    db.session.commit()
    flash('All data cleared (admin)', 'warning')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
