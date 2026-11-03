FROM eclipse-temurin:21-jdk AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN chmod +x mvnw && ./mvnw -B -q dependency:go-offline
COPY src src
# Tests need Docker (Testcontainers); they run via `make test`, not inside the image build.
RUN ./mvnw -B -q -DskipTests package

FROM eclipse-temurin:21-jre
RUN groupadd --system ledger && useradd --system --gid ledger ledger
WORKDIR /app
COPY --from=build /src/target/ledger-service.jar app.jar
USER ledger
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
