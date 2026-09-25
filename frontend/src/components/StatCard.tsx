type StatCardProps = {
  label: string;
  value: string | number;
  description: string;
};

function StatCard({ label, value, description }: StatCardProps) {
  return (
    <div className="stat-card">
      <p className="stat-card-label">{label}</p>
      <h3 className="stat-card-value">{value}</h3>
      <p className="stat-card-description">{description}</p>
    </div>
  );
}

export default StatCard;