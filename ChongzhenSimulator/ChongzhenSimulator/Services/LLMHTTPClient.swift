import Foundation

struct LLMChatMessage: Encodable {
    let role: String
    let content: String
}

struct LLMTextGenerationRequest: Encodable {
    let messages: [LLMChatMessage]
    let temperature: Double?
    let maxTokens: Int?

    enum CodingKeys: String, CodingKey {
        case messages
        case temperature
        case maxTokens = "max_tokens"
    }
}

struct LLMImageGenerationRequest: Encodable {
    let prompt: String
    let imageSize: String?

    enum CodingKeys: String, CodingKey {
        case prompt
        case imageSize
    }
}

struct LLMTextGenerationResponse: Decodable {
    let text: String
}

struct LLMImageGenerationResponse: Decodable {
    let imageURL: URL?
    let base64Image: String?
    let mimeType: String?
}

enum LLMHTTPClientError: Error, LocalizedError {
    case invalidHTTPResponse
    case requestFailed(statusCode: Int, body: String)

    var errorDescription: String? {
        switch self {
        case .invalidHTTPResponse:
            return "The LLM API returned an invalid HTTP response."
        case .requestFailed(let statusCode, let body):
            return "The LLM API request failed with status \(statusCode): \(body)"
        }
    }
}

final class LLMHTTPClient {
    private let configuration: LLMAPIConfiguration
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(
        configuration: LLMAPIConfiguration,
        session: URLSession = .shared,
        encoder: JSONEncoder = JSONEncoder(),
        decoder: JSONDecoder = JSONDecoder()
    ) {
        self.configuration = configuration
        self.session = session
        self.encoder = encoder
        self.decoder = decoder
    }

    func generateText(_ request: LLMTextGenerationRequest) async throws -> LLMTextGenerationResponse {
        let endpoint = configuration.textGeneration
        let url = endpoint.baseURL.appending(path: "chat/completions")
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.setValue("Bearer \(endpoint.apiKey)", forHTTPHeaderField: "Authorization")
        urlRequest.httpBody = try encoder.encode(
            DeepSeekChatCompletionRequest(
                model: endpoint.model,
                messages: request.messages,
                temperature: request.temperature,
                maxTokens: request.maxTokens,
                thinking: DeepSeekThinkingConfig(type: "disabled"),
                stream: false
            )
        )

        let response = try await data(for: urlRequest, route: .textGeneration)
        let decoded = try decoder.decode(DeepSeekChatCompletionResponse.self, from: response)
        return LLMTextGenerationResponse(text: decoded.choices.first?.message.content ?? "")
    }

    func generateImage(_ request: LLMImageGenerationRequest) async throws -> LLMImageGenerationResponse {
        let endpoint = configuration.imageGeneration
        let url = endpoint.baseURL
            .appending(path: "models")
            .appending(path: "\(endpoint.model):generateContent")
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.setValue(endpoint.apiKey, forHTTPHeaderField: "x-goog-api-key")
        urlRequest.httpBody = try encoder.encode(
            GeminiGenerateContentRequest(
                contents: [
                    GeminiContent(
                        role: "user",
                        parts: [GeminiPart(text: request.prompt, inlineData: nil)]
                    )
                ],
                generationConfig: GeminiGenerationConfig(
                    imageConfig: GeminiImageConfig(
                        imageSize: request.imageSize ?? endpoint.defaultImageSize
                    )
                )
            )
        )

        let response = try await data(for: urlRequest, route: .imageGeneration)
        let decoded = try decoder.decode(GeminiGenerateContentResponse.self, from: response)
        let inlineData = decoded.candidates
            .flatMap { $0.content.parts }
            .compactMap(\.inlineData)
            .first

        return LLMImageGenerationResponse(
            imageURL: nil,
            base64Image: inlineData?.data,
            mimeType: inlineData?.mimeType
        )
    }

    private func data(
        for request: URLRequest,
        route: LLMServiceRoute
    ) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw LLMHTTPClientError.invalidHTTPResponse
        }

        guard (200..<300).contains(httpResponse.statusCode) else {
            let responseBody = String(data: data, encoding: .utf8) ?? ""
            throw LLMHTTPClientError.requestFailed(statusCode: httpResponse.statusCode, body: "\(route.displayName): \(responseBody)")
        }

        return data
    }
}

private struct DeepSeekChatCompletionRequest: Encodable {
    let model: String
    let messages: [LLMChatMessage]
    let temperature: Double?
    let maxTokens: Int?
    let thinking: DeepSeekThinkingConfig
    let stream: Bool

    enum CodingKeys: String, CodingKey {
        case model
        case messages
        case temperature
        case maxTokens = "max_tokens"
        case thinking
        case stream
    }
}

private struct DeepSeekThinkingConfig: Encodable {
    let type: String
}

private struct DeepSeekChatCompletionResponse: Decodable {
    let choices: [Choice]

    struct Choice: Decodable {
        let message: Message
    }

    struct Message: Decodable {
        let content: String
    }
}

private struct GeminiGenerateContentRequest: Encodable {
    let contents: [GeminiContent]
    let generationConfig: GeminiGenerationConfig
}

private struct GeminiContent: Encodable, Decodable {
    let role: String?
    let parts: [GeminiPart]
}

private struct GeminiPart: Encodable, Decodable {
    let text: String?
    let inlineData: GeminiInlineData?
}

private struct GeminiInlineData: Encodable, Decodable {
    let mimeType: String
    let data: String
}

private struct GeminiGenerationConfig: Encodable {
    let imageConfig: GeminiImageConfig
}

private struct GeminiImageConfig: Encodable {
    let imageSize: String?
}

private struct GeminiGenerateContentResponse: Decodable {
    let candidates: [Candidate]

    struct Candidate: Decodable {
        let content: GeminiContent
    }
}
