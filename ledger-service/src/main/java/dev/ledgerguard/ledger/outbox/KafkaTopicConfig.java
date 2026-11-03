package dev.ledgerguard.ledger.outbox;

import dev.ledgerguard.ledger.config.LedgerProperties;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

@Configuration
@ConditionalOnProperty(name = "ledger.outbox.enabled", havingValue = "true", matchIfMissing = true)
public class KafkaTopicConfig {

    /** Created on startup by Spring's KafkaAdmin if missing. Single broker → replication factor 1. */
    @Bean
    NewTopic ledgerEventsTopic(LedgerProperties props) {
        return TopicBuilder.name(props.outbox().topic())
                .partitions(props.outbox().partitions())
                .replicas(1)
                .build();
    }
}
