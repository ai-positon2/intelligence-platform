// Kept for potential external use — main stat cards are now inline in App.tsx
export function StatCard({ label, value, sub, icon, color = 'bg-indigo-50 text-indigo-600' }: {
  label: string; value: string | number; sub?: string;
  icon: React.ReactNode; color?: string;
}) {
  return (
    <div className={`bg-white rounded-2xl border border-slate-200 p-5 flex items-start gap-4 shadow-sm card-lift`}>
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0 ${color}`}>{icon}</div>
      <div>
        <p className="text-2xl font-black text-slate-900 leading-none">{value}</p>
        <p className="text-sm font-medium text-slate-600 mt-1">{label}</p>
        {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}
