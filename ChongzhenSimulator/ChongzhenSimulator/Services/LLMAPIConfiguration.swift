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
    let defaultImageSize: String?
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
                route: .textGeneration,
                prefix: "CHONGZHEN_TEXT_API",
                environment: environment,
                bundle: bundle
            ),
            imageGeneration: try endpoint(
                route: .imageGeneration,
                prefix: "CHONGZHEN_IMAGE_API",
                environment: environment,
                bundle: bundle
            )
        )
    }

    private static func endpoint(
        route: LLMServiceRoute,
        prefix: String,
        environment: [String: String],
        bundle: Bundle
    ) throws -> LLMEndpointConfiguration {
        let baseURLKey = "\(prefix)_BASE_URL"
        let apiKeyKey = "\(prefix)_KEY"
        let modelKey = "\(prefix)_MODEL"
        let imageSizeKey = "\(prefix)_IMAGE_SIZE"

        let baseURLString = value(
            for: baseURLKey,
            environment: environment,
            bundle: bundle,
            defaultValue: route.defaultBaseURLString
        )
        guard let baseURL = URL(string: baseURLString) else {
            throw LLMConfigurationError.invalidURL(baseURLKey)
        }

        return LLMEndpointConfiguration(
            baseURL: baseURL,
            apiKey: try value(for: apiKeyKey, environment: environment, bundle: bundle),
            model: value(
                for: modelKey,
                environment: environment,
                bundle: bundle,
                defaultValue: route.defaultModel
            ),
            defaultImageSize: value(
                for: imageSizeKey,
                environment: environment,
                bundle: bundle,
                defaultValue: route.defaultImageSize
            )
        )
    }

    private static func value(
        for key: String,
        environment: [String: String],
        bundle: Bundle,
        defaultValue: String?
    ) -> String {
        if let value = environment[key], value.isEmpty == false {
            return value
        }

        if let value = bundle.object(forInfoDictionaryKey: key) as? String, value.isEmpty == false {
            return value
        }

        return defaultValue ?? ""
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

private extension LLMServiceRoute {
    var defaultBaseURLString: String {
        switch self {
        case .textGeneration:
            return "https://api.deepseek.com"
        case .imageGeneration:
            return "https://generativelanguage.googleapis.com/v1beta"
        }
    }

    var defaultModel: String {
        switch self {
        case .textGeneration:
            return "deepseek-v4-flash"
        case .imageGeneration:
            return "gemini-3.1-flash-image"
        }
    }

    var defaultImageSize: String? {
        switch self {
        case .textGeneration:
            return nil
        case .imageGeneration:
            return "1K"
        }
    }
}
