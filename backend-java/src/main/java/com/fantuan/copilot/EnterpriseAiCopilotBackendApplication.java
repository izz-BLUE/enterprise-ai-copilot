package com.fantuan.copilot;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class EnterpriseAiCopilotBackendApplication {

	public static void main(String[] args) {
		SpringApplication.run(EnterpriseAiCopilotBackendApplication.class, args);
	}

}
