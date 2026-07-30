"""
Online-presence tracking.

Bumps ``User.last_seen`` on each authenticated request so the super-admin
active-users table can show a live "online now" green dot. Throttled via the
session to at most one DB write per ``BUMP_EVERY_SECONDS`` so it costs
essentially nothing on hot paths.
"""
import time

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

SESSION_KEY = "_last_seen_bump"
BUMP_EVERY_SECONDS = 60


class LastSeenMiddleware(MiddlewareMixin):
    def process_request(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        session = getattr(request, "session", None)
        now = time.time()
        last = session.get(SESSION_KEY, 0) if session is not None else 0
        if now - last < BUMP_EVERY_SECONDS:
            return None
        # Update only this column; avoid touching the rest of the row.
        # ``request.user`` is a lazy proxy, so resolve the real model class.
        get_user_model().objects.filter(pk=user.pk).update(last_seen=timezone.now())
        if session is not None:
            session[SESSION_KEY] = now
        return None
