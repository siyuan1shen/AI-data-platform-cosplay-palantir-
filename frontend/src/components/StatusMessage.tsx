interface StatusMessageProps {
  title: string;
  description: string;
  tone?: "neutral" | "danger";
  action?: { label: string; onClick: () => void };
}

export function StatusMessage({ title, description, tone = "neutral", action }: StatusMessageProps) {
  return (
    <div className={`status-message ${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
      </div>
      {action && <button className="button secondary" onClick={action.onClick}>{action.label}</button>}
    </div>
  );
}
