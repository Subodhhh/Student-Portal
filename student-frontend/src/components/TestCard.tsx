type TestCardProps = {
  title: string;
  description?: string;
  date?: string;
  duration?: string;
  questions?: number;
  actionLabel?: string;
  onAction?: () => void;
};

function TestCard({
  title,
  description,
  date,
  duration,
  questions,
  actionLabel = "View Test",
  onAction,
}: TestCardProps) {
  return (
    <div className="test-card">
      <div className="test-card-content">
        <h3>{title}</h3>

        {description && <p>{description}</p>}

        <div className="test-card-meta">
          {date && <span>{date}</span>}
          {duration && <span>{duration}</span>}
          {questions !== undefined && <span>{questions} Questions</span>}
        </div>
      </div>

      <button onClick={onAction}>{actionLabel}</button>
    </div>
  );
}

export default TestCard;