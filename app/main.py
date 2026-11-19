import csv,io,json
from flask import Flask,request,jsonify,redirect,Response
from importlib import import_module
from app.config import STAGES,MAX_BYTES
from app.errors import DomainError
from app.normalize import serial
from app.pipeline import run
from app import store
from app.web import view
def create_app():
    app=Flask(__name__);app.config["MAX_CONTENT_LENGTH"]=MAX_BYTES
    app.json.ensure_ascii=False
    @app.errorhandler(DomainError)
    def domain_error(error): return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(413)
    def large(error): return jsonify(code="TOO_LARGE",message="文件超过4MB"),413
    @app.get("/health")
    def health(): return {"status":"ok"}
    @app.get("/")
    def home(): return redirect("/m/1")
    @app.get("/m/<int:stage>")
    def page(stage): return view(stage)
    @app.post("/api/runs")
    def start():
        if request.files:
            file=request.files.get("file")
            if file is None: raise DomainError("FILE","请选择文件")
            raw=file.read(MAX_BYTES+1)
            try: params=json.loads(request.form.get("params","{}"))
            except ValueError: raise DomainError("PARAMS","参数格式错误")
        else:
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict): raise DomainError("BODY","请求必须是对象")
            raw=payload.get("text")
            if raw is not None and not isinstance(raw,str): raise DomainError("TEXT","数据必须为文本")
            params=payload.get("params")
        return jsonify(run(raw,params))
    @app.get("/api/runs/<run_id>")
    def get_run(run_id): return jsonify(store.fetch(run_id))
    @app.get("/api/history")
    def history(): return jsonify(store.history())
    @app.post("/api/review/<run_id>")
    def review(run_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict): raise DomainError("BODY","请求必须是对象")
        return jsonify(store.review(run_id,data.get("version"),data.get("decision")))
    @app.get("/api/evidence.csv")
    def evidence():
        result=store.fetch(request.args["run"]) if request.args.get("run") else run()
        stream=io.StringIO(newline="");writer=csv.writer(stream)
        writer.writerow(["批次","行号","主键","原因码","证据"])
        for row in result.get("records",[]):
            writer.writerow([result["manifest"]["run_id"],row.get("_line",""),row.get("id",row.get("event_id","")),"PASS",json.dumps(serial(row),ensure_ascii=False)])
        for row in result["quarantine"]:
            writer.writerow([result["manifest"]["run_id"],row["line"],row["key"],row["code"],json.dumps(row,ensure_ascii=False)])
        return Response("\ufeff"+stream.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=evidence.csv"})
    @app.get("/api/settlement.csv")
    def settlement_csv():
        result=store.fetch(request.args["run"]) if request.args.get("run") else run()
        data=result.get("settlement")
        if data is None: raise DomainError("STAGE","\u8be5\u6279\u6b21\u4e0d\u542b\u7ed3\u7b97\u5e8a\u65e5\uff0c\u8bf7\u91cd\u65b0\u8fd0\u884c",404)
        run_id=result["manifest"]["run_id"]
        stream=io.StringIO(newline="");writer=csv.writer(stream)
        writer.writerow(["\u7c7b\u522b","\u6279\u6b21","\u65e5\u671f","\u60a3\u8005\u53f7","\u5f52\u5c5e\u75c5\u533a","\u5e8a\u4f4d\u65e5","\u5360\u5e8a\u5206\u949f","\u65e5\u5386\u5206\u949f","\u5206\u6bb5\u5206\u949f","\u5dee\u5f02\u5206\u949f","\u75c5\u533a\u660e\u7ec6","\u5f52\u5c5e\u8bf4\u660e","\u72b6\u6001"])
        for row in data["rows"]:
            writer.writerow(["\u7ed3\u7b97\u5e8a\u65e5",run_id,row["date"],row["patient"],row["ward"],1,row["minutes"],"","", "", row["wards_text"],row["basis"],row["state"]])
        for day in data["ward_days"]:
            writer.writerow(["\u75c5\u533a\u65e5\u5408\u8ba1",run_id,day["date"],"",day["ward"],day["bed_days"],day["minutes"],day["calendar_minutes"],"",day["delta"],"","",day["state"]])
        for total in data["ward_totals"]:
            writer.writerow(["\u75c5\u533a\u5408\u8ba1",run_id,"","",total["ward"],total["bed_days"],total["minutes"],total["calendar_minutes"],total["episode_minutes"],total["delta"],"","",total["state"]])
        return Response("\ufeff"+stream.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=settlement.csv"})
    for n,name in enumerate(STAGES,1): app.register_blueprint(import_module(f"app.modules.m{n}_{name}.handler").bp)
    return app
app=create_app()
