import Foundation

struct LLMChatMessage: Encodable {
    let role: String
    let content: String
}

struct LLMTextGenerationRequest: Encodable {
    let messages: [LLMChatMessage]
    let temperature: Double?
    let maxTokens: Int?
}

struct LLMImageGenerationRequest: Encodable {
    let prompt: String
    let size: String?
}

struct LLMTextGenerationResponse: Decodable {
    let text: String
}

struct LLMImageGenerationResponse: Decodable {
    let imageURL: URL?
    let base64Image: String?
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
        return try await send(
            route: .textGeneration,
            path: "generate",
            body: request,
            responseType: LLMTextGenerationResponse.self
        )
    }

    func generateImage(_ request: LLMImageGenerationRequest) async throws -> LLMImageGenerationResponse {
        return try await send(
            route: .imageGeneration,
            path: "generate",
            body: request,
            responseType: LLMImageGenerationResponse.self
        )
    }

    private func send<RequestBody: Encodable, ResponseBody: Decodable>(
        route: LLMServiceRoute,
        path: String,
        body: RequestBody,
        responseType: ResponseBody.Type
    ) async throws -> ResponseBody {
        let endpoint = configuration[route]
        let url = endpoint.baseURL.appending(path: path)
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.setValue("Bearer \(endpoint.apiKey)", forHTTPHeaderField: "Authorization")
        urlRequest.httpBody = try encoder.encode(LLMRequestEnvelope(model: endpoint.model, input: body))

        let (data, response) = try await session.data(for: urlRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw LLMHTTPClientError.invalidHTTPResponse
        }

        guard (200..<300).contains(httpResponse.statusCode) else {
            let responseBody = String(data: data, encoding: .utf8) ?? ""
            throw LLMHTTPClientError.requestFailed(statusCode: httpResponse.statusCode, body: responseBody)
        }

        return try decoder.decode(responseType, from: data)
    }
}

private struct LLMRequestEnvelope<Input: Encodable>: Encodable {
    let model: String
    let input: Input
}
