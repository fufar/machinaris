import asyncio
import datetime as dt

from flask.views import MethodView

from api import app
from api.rpc import chia
from api.extensions.api import Blueprint, SQLCursorPage
from common.extensions.database import db
from common.models import Plot

from .schemas import PlotSchema, PlotQueryArgsSchema, BatchOfPlotSchema, BatchOfPlotQueryArgsSchema

blp = Blueprint(
    'Plot',
    __name__,
    url_prefix='/plots',
    description="Operations on all plots on farmer"
)

@blp.route('/')
class Plots(MethodView):

    @blp.etag
    @blp.arguments(BatchOfPlotQueryArgsSchema, location='query')
    @blp.response(200, PlotSchema(many=True))
    @blp.paginate(SQLCursorPage)
    def get(self, args):
        ret = Plot.query.filter_by(**args)
        return ret

    @blp.etag
    @blp.arguments(BatchOfPlotSchema)
    @blp.response(201, PlotSchema(many=True))
    def post(self, new_items):
        # Get plot info via RPC to determine type: solo or portable
        plots_via_rpc = chia.get_all_plots()
        # Now delete all old plots by hostname of first new plotting
        db.session.query(Plot).filter(Plot.hostname==new_items[0]['hostname']).delete()
        items = []
        for new_item in new_items:
            item = Plot(**new_item)
            item.type = ""
            for plot_rpc in plots_via_rpc:
                if plot_rpc['plot_id'].startswith("0x{0}".format(item.plot_id)) and 'type' in plot_rpc:
                    item.type = plot_rpc['type']
                    #app.logger.info("Found type: {0}".format(item.type))
            db.session.add(item)
            items.append(item)
        db.session.commit()
        return items


@blp.route('/<hostname>')
class PlotByHostname(MethodView):

    @blp.etag
    @blp.response(200, PlotSchema)
    def get(self, hostname):
        return db.session.query(Plot).filter(Plot.hostname==hostname)

    @blp.etag
    @blp.arguments(BatchOfPlotSchema)
    @blp.response(200, PlotSchema(many=True))
    def put(self, new_items, hostname):
        # Now delete all old plots by hostname of first new plotting
        db.session.query(Plot).filter(Plot.hostname==hostname).delete()
        items = []
        for new_item in new_items:
            item = Plot(**new_item)
            items.append(item)
            db.session.add(item)
        db.session.commit()
        return items

    @blp.etag
    @blp.response(204)
    def delete(self, hostname):
        db.session.query(Plot).filter(Plot.hostname==hostname).delete()
        db.session.commit()