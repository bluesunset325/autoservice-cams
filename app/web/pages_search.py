from fastapi import APIRouter, Depends, Request

from app.plate_fmt import normalize_query
from app.users import User
from app.web.auth import current_user, render
from app.web.pages_journal import parse_date

router = APIRouter()
SEARCH_LIMIT = 500


@router.get("/search")
def search(request: Request, q: str = "", date_from: str = "", date_to: str = "",
           user: User = Depends(current_user)):
    state = request.app.state
    query = normalize_query(q)
    d_from, d_to = parse_date(date_from), parse_date(date_to)
    searched = bool(query or d_from or d_to)
    results = state.visits.search(query, d_from, d_to, limit=SEARCH_LIMIT) if searched else []
    return render(
        request, "search.html", user, nav="search",
        q=q, date_from=date_from, date_to=date_to, results=results, searched=searched,
        limit=SEARCH_LIMIT, now=state.clock(), cameras={c.id: c.name for c in state.cfg.cameras},
    )
