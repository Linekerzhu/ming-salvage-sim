import Foundation

enum LLMServiceUseCase: String, CaseIterable {
    case developmentSupport
    case inGameRuntime

    var displayName: String {
        switch self {
        case .developmentSupport:
            return "开发支持"
        case .inGameRuntime:
            return "游戏运行"
        }
    }
}

enum LLMServiceRoute: String, CaseIterable {
    case textGeneration
    case imageGeneration

    var displayName: String {
        switch self {
        case .textGeneration:
            return "文字生成"
        case .imageGeneration:
            return "图像生成"
        }
    }

    var supportedUseCases: [LLMServiceUseCase] {
        return [.developmentSupport, .inGameRuntime]
    }

    var purpose: String {
        switch self {
        case .textGeneration:
            return "开发期剧本文学与数值支持；游戏内对白、奏疏、事件叙述与决策生成。"
        case .imageGeneration:
            return "开发期插画、图标与美术资产支持；游戏内过程插画、头像与图像反馈生成。"
        }
    }
}

struct LLMEndpointConfiguration {
    let baseURL: URL
    let apiKey: String
    let model: String
}

struct LLMAPIConfiguration {
    let textGeneration: LLMEndpointConfiguration
    let imageGeneration: LLMEndpointConfiguration

    subscript(route: LLMServiceRoute) -> LLMEndpointConfiguration {
        switch route {
        case .textGeneration:
            return textGeneration
        case .imageGeneration:
            return imageGeneration
        }
    }
}

enum LLMConfigurationError: Error, LocalizedError {
    case missingValue(String)
    case invalidURL(String)

    var errorDescription: String? {
        switch self {
        case .missingValue(let key):
            return "Missing LLM configuration value: \(key)"
        case .invalidURL(let key):
            return "Invalid LLM configuration URL: \(key)"
        }
    }
}

enum LLMConfigurationLoader {
    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        bundle: Bundle = .main
    ) throws -> LLMAPIConfiguration {
        return LLMAPIConfiguration(
            textGeneration: try endpoint(
                prefix: "CHONGZHEN_TEXT_API",
                environment: environment,
                bundle: bundle
            ),
            imageGeneration: try endpoint(
                prefix: "CHONGZHEN_IMAGE_API",
                environment: environment,
                bundle: bundle
            )
        )
    }

    private static func endpoint(
        prefix: String,
        environment: [String: String],
        bundle: Bundle
    ) throws -> LLMEndpointConfiguration {
        let baseURLKey = "\(prefix)_BASE_URL"
        let apiKeyKey = "\(prefix)_KEY"
        let modelKey = "\(prefix)_MODEL"

        let baseURLString = try value(for: baseURLKey, environment: environment, bundle: bundle)
        guard let baseURL = URL(string: baseURLString) else {
            throw LLMConfigurationError.invalidURL(baseURLKey)
        }

        return LLMEndpointConfiguration(
            baseURL: baseURL,
            apiKey: try value(for: apiKeyKey, environment: environment, bundle: bundle),
            model: try value(for: modelKey, environment: environment, bundle: bundle)
        )
    }

    private static func value(
        for key: String,
        environment: [String: String],
        bundle: Bundle
    ) throws -> String {
        if let value = environment[key], value.isEmpty == false {
            return value
        }

        if let value = bundle.object(forInfoDictionaryKey: key) as? String, value.isEmpty == false {
            return value
        }

        throw LLMConfigurationError.missingValue(key)
    }
}
