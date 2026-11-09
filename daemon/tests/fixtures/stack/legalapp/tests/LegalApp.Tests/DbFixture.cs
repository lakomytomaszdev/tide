using Testcontainers.PostgreSql;

public class DbFixture
{
    public PostgreSqlContainer Db { get; } = new PostgreSqlBuilder("pgvector/pgvector:pg16").Build();
}
