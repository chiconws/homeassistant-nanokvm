# Gemini Project Overview: Sipeed NanoKVM Home Assistant Integration (Code-centric)

**Developer Note:** This document is maintained by the LLM assistant developing this integration. It should be kept up-to-date to reflect the current architecture and implementation details of the project.

This document provides a developer-focused overview of the Sipeed NanoKVM Home Assistant integration, detailing its architecture and code structure.

## Project Overview

This project is a custom integration for [Home Assistant](https://www.home-assistant.io/), allowing users to control and monitor a [Sipeed NanoKVM](https://github.com/sipeed/NanoKVM) device. It communicates with the NanoKVM's HTTP API to expose its features as entities and services in Home Assistant.

The integration is built upon the `nanokvm` Python library, which handles the low-level API communication.

## Code Structure

The integration follows the standard structure for a Home Assistant `custom_component`.

-   `custom_components/nanokvm/`: The root directory for the integration.

### Core Files

-   **`__init__.py`**: The main entry point.
    -   **`async_setup_entry`**: Initializes the integration from a config entry. It sets up the `NanoKVMDataUpdateCoordinator` and forwards the setup to the various entity platforms (binary_sensor, switch, etc.).
    -   **`NanoKVMDataUpdateCoordinator`**: A central class that manages fetching data from the NanoKVM device at a regular interval. It uses the `nanokvm` client to perform API calls and stores the latest state. All API calls within its `_async_update_data` method are wrapped in an `async with self.client:` block to ensure the client's session is managed correctly for each update cycle.
    -   **Service Registration**: Defines and registers the custom services. To avoid boilerplate code, a generic `_execute_service` helper function is defined within `async_setup_entry`. This helper iterates through all configured devices, wraps the core service logic in the required `async with client:` block, and centralizes error logging. Each service handler then calls this helper with its specific logic.
    -   **`NanoKVMEntity`**: A base class for all entities in this integration. It inherits from `CoordinatorEntity` and provides common properties like `device_info`.

-   **`config_flow.py`**: Manages the user-facing configuration process in the Home Assistant UI.
    -   Implements `ConfigFlow` to handle both manual setup (user provides the device's host) and automatic discovery via `zeroconf`.
    -   It includes a `validate_input` function that attempts to connect to the device and authenticate to ensure the provided credentials are valid before creating the config entry.
    -   Handles authentication, prompting for a username and password if the default credentials fail.

-   **`const.py`**: A central repository for constants used throughout the integration. This includes the integration `DOMAIN`, service names, attribute names, and icon definitions.

-   **`manifest.json`**: Contains metadata for the integration.
    -   Specifies the domain, name, version, and dependencies (like `zeroconf`).
    -   Lists the PyPI requirements (`nanokvm`).
    -   Defines the `iot_class` as `local_polling`, indicating it communicates with a local device over the network.
    -   Includes the `zeroconf` discovery trigger.

### Entity Platforms

The integration is divided into multiple platform files, each responsible for a specific type of entity in Home Assistant.

-   `binary_sensor.py`
-   `button.py`
-   `camera.py`
-   `select.py`
-   `sensor.py`
-   `switch.py`

Each of these files follows a similar pattern:
1.  **Entity Descriptions**: A tuple of dataclass instances (e.g., `NanoKVMSwitchEntityDescription`) declaratively defines each entity. These descriptions hold the entity's `key`, `name`, `icon`, and other static properties.
2.  **`value_fn`**: The entity descriptions include a `value_fn` lambda function. This function takes the `coordinator` as an argument and extracts the correct state for that specific entity from the coordinator's data. This is a key part of the coordinator pattern.
3.  **Action Functions**: For entities that support actions (like `SwitchEntity` or `ButtonEntity`), the descriptions include functions like `turn_on_fn` or `press_fn`. These functions return a coroutine for the specific client method.
4.  **`async_setup_entry`**: This function in each platform file is called by Home Assistant to set up the entities. It iterates through the entity descriptions and creates an instance of the corresponding entity class for each.
5.  **Entity Class**: A class (e.g., `NanoKVMSwitch`) that inherits from a base Home Assistant entity class and the integration's `NanoKVMEntity`. The action methods within this class (e.g., `async_turn_on`, `async_press`) are responsible for wrapping the call to the action function from the entity description in an `async with self.coordinator.client:` block. This ensures proper session management for all entity actions.

### Services

-   **`services.yaml`**: Defines the custom services that the integration exposes to Home Assistant. This file describes the service names, descriptions, and the fields (parameters) they accept, which enables the Home Assistant UI to display them correctly. The implementation of these services is in `__init__.py`.

## Key Concepts

-   **`NanoKVMClient` Lifecycle Management**: The `nanokvm` library requires the `NanoKVMClient` to be used as an async context manager for all API operations to ensure its internal `aiohttp.ClientSession` is handled correctly. Even though a persistent `NanoKVMClient` instance is stored in the coordinator, every operation or logical group of operations (e.g., a full data refresh, a service call, an entity action) must be wrapped in an `async with client:` block. This pattern ensures the session is opened before the operation and closed immediately after, preventing session-related errors.
-   **Coordinator Pattern**: The use of `DataUpdateCoordinator` is central to this integration's design. It provides a single point of contact for fetching data from the NanoKVM device, which is then distributed to all the associated entities. This is highly efficient and is the recommended approach for polling-based integrations in Home Assistant.
-   **Declarative Entities**: Entities are defined using dataclasses (`...EntityDescription`). This makes the code clean, readable, and easy to maintain. Adding a new entity is as simple as adding a new description to the appropriate tuple.
-   **`nanokvm` Library**: The integration does not implement the API communication logic itself. Instead, it relies on the `nanokvm` Python library. This separation of concerns is a good practice, as it allows the library to be developed and tested independently.
-   **Zeroconf Discovery**: The integration can automatically discover NanoKVM devices on the local network using Zeroconf (mDNS), providing a better user experience.
