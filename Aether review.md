## Overall Project Architecture
High-level folder structure: aether/
- core/
- adapters/
- config/
- scripts/
- tests/
- data/
- logs/
Entry points and main execution flow: The project utilizes a modular design with distinct areas for data handling, context engine, features, strategy, and delivery mechanisms. The main entry point is through the aether gateway, which initializes the necessary components based on configuration.
Data flow diagrams: Data flows from data providers (e.g., Oanda) into the system, where it is normalized and processed by the context engine and feature calculations. The output is then used by the strategy engine to generate signals, which are validated and delivered to the user through adapters (e.g., Telegram).
How the parts communicate: Components communicate through defined interfaces and APIs, ensuring loose coupling and maintainability. For instance, strategies interact with the data layer through a standardized data access object, and adapters receive signals through a messaging system.
Summary of how the whole project works: The Aether project is designed as a modular, scalable system for generating and delivering trading signals. It integrates with various data providers, calculates market context and features, applies user-configurable strategies, validates signals, and notifies users through preferred messaging platforms. The system prioritizes determinism, reliability, and extensibility, with a focus on user configuration and customization.