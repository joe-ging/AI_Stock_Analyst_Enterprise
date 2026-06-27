const RagasDashboard = ({ scores, t }) => {
    const metrics = [
        {
            key: "faithfulness", 
            label: "Faithfulness (忠实度)", 
            desc: "正文的所有数据和结论是否100%忠实于原文，有无捏造或模型幻觉。",
            style: { bg: "bg-indigo-50/50", border: "border-indigo-100/50", text: "text-indigo-600", valText: "text-indigo-800", bar: "bg-indigo-500" }
        },
        {
            key: "answer_recall", 
            label: "Answer Recall (召回率)", 
            desc: "报告是否完整覆盖了原文中分析该问题所需的所有关键财务与业务指标。",
            style: { bg: "bg-emerald-50/50", border: "border-emerald-100/50", text: "text-emerald-600", valText: "text-emerald-800", bar: "bg-emerald-500" }
        },
        {
            key: "relevance", 
            label: "Relevance (相关度)", 
            desc: "生成的报告内容与股票分析及投资决策的相关程度。",
            style: { bg: "bg-violet-50/50", border: "border-violet-100/50", text: "text-violet-600", valText: "text-violet-800", bar: "bg-violet-500" }
        }
    ];

    return (
        <div className="mt-6 border-t border-gray-100 pt-8">
            <div className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-4">
                📊 RAG RETRIEVAL QUALITY AUDIT (RAGAS SCORE)
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {metrics.map(({key, label, desc, style}) => {
                    const val = scores ? (scores[key] ?? 0) : null;
                    const pct = val !== null ? Math.round(val * 100) : null;
                    return (
                        <div key={key} className={`${style.bg} border ${style.border} rounded-2xl p-5 relative overflow-hidden flex flex-col justify-between min-h-[160px] shadow-sm`}>
                            {pct === null && (
                                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/50 to-transparent animate-pulse" />
                            )}
                            <div>
                                <div className="flex justify-between items-baseline mb-2">
                                    <span className={`text-xs font-black tracking-tight ${style.text}`}>{label}</span>
                                    <span className={`text-3xl font-black ${style.valText}`}>
                                        {pct !== null ? `${pct}%` : <span className="text-xs font-bold text-gray-400 animate-pulse">Auditing...</span>}
                                    </span>
                                </div>
                                <p className="text-[10px] text-slate-500 leading-normal font-medium mb-4">{desc}</p>
                            </div>
                            <div className="h-2 bg-white rounded-full overflow-hidden shadow-inner w-full">
                                <div 
                                    className={`h-full ${style.bar} rounded-full transition-all duration-1000`}
                                    style={{width: `${pct !== null ? pct : 0}%`}}
                                />
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};
window.RagasDashboard = RagasDashboard;
